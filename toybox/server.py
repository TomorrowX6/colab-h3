"""H3 Toybox - a cute web frontend for MiniMax H3 on top of a running ComfyUI.

Modes
  ref     Ref2VA: up to 9 images / 3 videos / 3 audios as references (@圖片1 ...).
          One tray video can be marked "extend": its tail is pinned into the head of the
          new clip (H3 Motion Context) and the result is joined back onto the source.
  frames  I2VA / FL2VA / L2VA: first frame, last frame, or both (MiniMaxH3ImageToVideo).
  text    T2VA: text only.

Run with ComfyUI's embedded python (stdlib + PIL + av, all already installed):
    F:\\CUI\\python_embeded\\python.exe -I server.py
then open http://127.0.0.1:8190
"""
import base64
import copy
import io
import json
import mimetypes
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import av
except ImportError:
    av = None
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
TEMPLATE_PATH = ROOT / "WF.json"
# Everything the user makes lives under DATA (Colab points it at Google Drive so a dropped runtime loses nothing).
DATA = Path(os.environ.get("H3TOYBOX_DATA") or ROOT)
LIBRARY = DATA / "library"
OUTPUTS = DATA / "outputs"
CONFIG_PATH = DATA / "config.json"
JOBS_PATH = DATA / "jobs.json"
FOLDERS_PATH = LIBRARY / "folders.json"
STATS_PATH = DATA / "stats.json"
# API keys are kept apart from the settings, so settings can sync to Drive while keys stay local.
SECRETS_PATH = Path(os.environ.get("H3TOYBOX_SECRETS") or DATA / "secrets.json")
PORT = int(os.environ.get("H3TOYBOX_PORT", 8190))
FPS = 24

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}

DEFAULT_CONFIG = {
    "comfy_url": "http://127.0.0.1:8188",
    "comfy_input_dir": r"F:\CUI\ComfyUI\input",
    "guides": {
        "ref": str(ROOT / "guides" / "VIDEO_PROMPT_WRITING_GUIDE_ref_en.md"),
        "base": str(ROOT / "guides" / "VIDEO_PROMPT_WRITING_GUIDE_base_en.md"),
    },
    "skills_dir": r"F:\CUI\ComfyUI\custom_nodes\minimax-h3-seedance-music3-prompt-enhancer-t8\directional_skills",
    "rewriter": {
        "model": "minimax_h3_rewriter_ref2va_qwen3.5_9b_w4a8.safetensors",
        "thinking": False,
        "mtp": "auto",
        "max_length": 8192,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "image_side": 768,
        "video_frames": 8,
        # local = the quantized H3 rewriter inside ComfyUI; gemini / openai = remote endpoints
        "provider": "local",
        "gemini": {"api_key": "", "model": "gemini-3.8-flash"},
        "openai": {"base_url": "", "api_key": "", "model": ""},
    },
    # 快速模式 (default on): fewer SelfLift steps, half at low resolution and half at high
    "steps": {"normal": [12, 6], "fast": [8, 4]},
    # LoRA node ids in WF.json. "drama" LoRAs always apply; "action" LoRAs apply only
    # when motion intensity is on, with strength = template strength * multiplier.
    "loras": {
        "drama": ["53", "400", "404"],
        "action": ["342", "344", "343"],
    },
    "intensity_multipliers": {"low": 0.6, "mid": 1.0, "high": 1.3},
    # extension (H3 Motion Context): frames of the source tail pinned into the new clip
    "extend": {"context_length": "22", "audio_context_length": 24, "tail_seconds": 3.0},
    # Colab: unload the 9B rewriter right after it answers, so it never sits in RAM next to the H3 models
    "free_after_rewrite": False,
    # 🤖 短片 Agent: the planner that turns refs + one instruction into a segment contract.
    # openai = any OpenAI-compatible server (the local MoE on llama.cpp by default);
    # gemini = Google API, key read from key_file at call time (never copied into config or the browser)
    "agent": {
        "provider": "openai",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "qwen36-35b-a3b",
        "gemini_model": "gemini-3.5-flash-lite",
        "gemini_key_file": "",
        "temperature": 0.6,
        "max_seconds": 60,
    },
    # 🖼 圖片 tab: Qwen-Image 2.1 with ComfyUI's own template wiring (TextEncodeQwenImage21 + KSampler, cfg 1)
    "image": {
        # qwen = Qwen-Image 2.1 (desktop); ph3 = P-H3-Image, a LoRA on the H3 model (Colab: +10 GB instead of +20 GB)
        "engine": "qwen",
        "ph3": {
            "unet": "Minimax-h3_Singularity_x_10Eros-Max_PRISM_int8.safetensors",
            "lora": "p-h3-image_lora_bf16.safetensors",
            "clip": "p-h3-image_text_encoder_int8_convrot.safetensors",
            "vae": "p-h3-image_vae_int8_convrot.safetensors",
            "sigmas": "1.0, 0.984753, 0.967905, 0.951688, 0.925372, 0.881923, 0.810083, 0.528718, 0.0",
            "spec": str(ROOT / "guides" / "p_h3_image_spec.txt"),
        },
        "unet": "qwen_image_2.1_int8_convrot.safetensors",
        "clip": "qwen3vl_8b_int8_convrot.safetensors",
        "vae": "qwen_image_2.1_vae_bf16.safetensors",
        "steps": 25,
        "cfg": 1.0,
        "spec_t2i": str(ROOT / "guides" / "qwen_image_2.1_t2i_spec.txt"),
        "spec_edit": str(ROOT / "guides" / "qwen_image_2.1_edit_spec.txt"),
    },
}
# long side of a generated picture, per size button
IMAGE_SIZES = {"1K": 1024, "1.3K": 1344, "1.6K": 1664, "2K": 2048, "2.7K": 2720}  # P-H3 is best at 2K+

# Node ids in WF.json that the server rewires
N_COND, N_SAMPLER, N_SCHED, N_SHIFT, N_BRIDGE = "265", "397", "261", "333", "402"
N_VAE, N_AVAE, N_CLIP, N_LEN, N_SECONDS = "36", "37", "38", "250", "259"
N_DECODE, N_ADECODE = "253", "254"
PREVIEW_ONLY = ("401", "329", "364", "51", "252")  # ChunkFeedForward + preview override + VHS + sample inputs
N_IMG_ENC, N_IMG_SAMPLER, N_IMG_SAVE = "ienc", "isamp", "isave"  # node ids of the 🖼 圖片 graph

SYSTEM_REF = (
    "You enhance prompts for reference-conditioned video and audio generation (Ref2VA). "
    "Infer reference roles from the user's prompt and supplied media. "
    "Preserve intent and explicit constraints while adding relevant visual and sound details. "
    "Follow the guide below and return only its six sections in order, "
    "with plain-text headers on separate lines and no Markdown fences.\n\n"
)
SYSTEM_BASE = (
    "You enhance prompts for video and audio generation ({task}). "
    "Preserve intent and explicit constraints while adding relevant visual and sound details. "
    "Follow the guide below. Return the alignment instruction line if the task has one, then only the three core "
    "sections integrated_multimodal_description, overall_soundscape and non_diegetic_music, "
    "with plain-text headers and no Markdown fences.\n\n"
)
LABEL = {"image": "Picture", "video": "Video", "audio": "Audio"}
CHINESE_NAME = {"image": "图片", "video": "视频", "audio": "音频"}
# @圖片1 / @影片1 / @音訊1, plus H3Easy-style @Image1 / @Video1 / @Audio1
MENTION_RE = re.compile(r"@(圖片|图片|Image|影片|视频|Video|音訊|音频|Audio)\s?(\d+)", re.IGNORECASE)
MENTION_KIND = {"圖片": "image", "图片": "image", "image": "image", "影片": "video", "视频": "video",
                "video": "video", "音訊": "audio", "音频": "audio", "audio": "audio"}
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "21:9": (21, 9)}
VISION = "<|vision_start|><|image_pad|><|vision_end|>"
# Display names follow the T8 enhancer's docs/directional-skills.md
SKILL_NAMES = {
    "wushu_combat": "Jojocodex-武術打鬥", "high_density_combat": "土豆-高密度連續攻防",
    "continuous_combat": "Fisher-連續戰鬥長鏡頭", "cinematic_gunfight": "兔子-電影槍戰導演",
    "ning_wenwu": "寧版-文武雙全", "drama_scene": "戲劇場面｜關係與潛台詞",
    "situational_drama": "情境戲劇｜處境與鋪墊回收", "zhenzhen_pov": "貞貞-POV劇情導演",
}

lock = threading.Lock()


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return copy.deepcopy(default)


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def deep_merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


API_KEYS = (("rewriter", "gemini"), ("rewriter", "openai"))


def load_config():
    saved = load_json(CONFIG_PATH, {})
    saved.pop("guide_path", None)  # H3 Studio's single-guide key
    cfg = deep_merge(copy.deepcopy(DEFAULT_CONFIG), saved)
    secrets = load_json(SECRETS_PATH, {})
    for a, b in API_KEYS:
        if secrets.get(b):
            cfg[a][b]["api_key"] = secrets[b]
    return cfg


def public_config(cfg):
    """Config for the browser: API keys are never sent back, only whether one is stored."""
    out = copy.deepcopy(cfg)
    for a, b in API_KEYS:
        sect = out[a][b]
        sect["has_key"] = bool(sect.get("api_key"))
        sect["api_key"] = ""
    return out


def save_config(new):
    """An empty api_key from the form keeps the stored key; "__CLEAR__" removes it."""
    old = load_config()
    new = {k: new[k] for k in DEFAULT_CONFIG if k in new}
    for a, b in API_KEYS:
        sect = new.get(a, {}).get(b)
        if isinstance(sect, dict):
            sect.pop("has_key", None)
            key = sect.get("api_key", "")
            sect["api_key"] = "" if key == "__CLEAR__" else key.strip() or old[a][b].get("api_key", "")
    secrets = {}
    for a, b in API_KEYS:
        sect = new.get(a, {}).get(b)
        key = sect.pop("api_key", "") if isinstance(sect, dict) else old[a][b].get("api_key", "")
        if key:
            secrets[b] = key
    stored = deep_merge(load_json(CONFIG_PATH, {}), new)
    for a, b in API_KEYS:  # keys written by older versions into config.json
        stored.get(a, {}).get(b, {}).pop("api_key", None)
    save_json(CONFIG_PATH, stored)
    if secrets or SECRETS_PATH.exists():
        SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_json(SECRETS_PATH, secrets)


def restore_inputs(cfg):
    """Library assets also live in ComfyUI's input folder. A fresh runtime (or a new ComfyUI) starts with
    that folder empty while the library on Drive is intact, so put the copies back before the first job."""
    inp = Path(cfg["comfy_input_dir"])
    if not inp.is_dir():
        return 0
    restored = 0
    for item in library_index():
        dest, src = inp / item["comfy_name"], LIBRARY / item["file"]
        if not dest.exists() and src.exists():
            shutil.copyfile(src, dest)
            restored += 1
    return restored


# ---------------------------------------------------------------- ComfyUI client

def comfy(cfg, path, payload=None, timeout=60):
    url = cfg["comfy_url"].rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def queue_prompt(cfg, wf):
    try:
        return comfy(cfg, "/prompt", {"prompt": wf, "client_id": MONITOR.client_id})["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError("ComfyUI 拒絕了工作流：" + e.read().decode("utf-8", "replace")[:2000])


def history_entry(cfg, prompt_id):
    return comfy(cfg, f"/history/{prompt_id}").get(prompt_id)


def wait_prompt(cfg, prompt_id, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        entry = history_entry(cfg, prompt_id)
        if entry:
            if entry["status"].get("status_str") == "error":
                raise RuntimeError(execution_error(entry))
            return entry
        time.sleep(1.0)
    raise RuntimeError("等待 ComfyUI 逾時")


def execution_error(entry):
    for name, msg in entry["status"].get("messages", []):
        if name == "execution_error":
            return f"{msg.get('node_type')}: {msg.get('exception_message', '').strip()}"
    return "ComfyUI 執行失敗"


def comfy_download(cfg, fileinfo, dest):
    q = urllib.parse.urlencode({"filename": fileinfo["filename"], "subfolder": fileinfo.get("subfolder", ""),
                                "type": fileinfo.get("type", "output")})
    with urllib.request.urlopen(cfg["comfy_url"].rstrip("/") + "/view?" + q, timeout=600) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


# ---------------------------------------------------------------- progress monitor (stdlib websocket)

class Monitor(threading.Thread):
    """Listens to ComfyUI's websocket so jobs can show their stage, step and a time estimate.

    Prompts are queued with this client id, so ComfyUI sends their progress events here.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.client_id = "h3toybox-" + uuid.uuid4().hex[:8]
        self.state = {}          # prompt_id -> live state
        self.connected = False
        self.mutex = threading.Lock()

    def run(self):
        while True:
            try:
                self.listen(load_config()["comfy_url"])
            except Exception:
                pass
            self.connected = False
            time.sleep(3)

    def listen(self, comfy_url):
        u = urllib.parse.urlparse(comfy_url)
        host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=30)
        if u.scheme == "https":
            import ssl
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {u.path.rstrip('/')}/ws?clientId={self.client_id} HTTP/1.1\r\nHost: {host}:{port}\r\n"
                      f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                      "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1024)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket refused")
        buf = head.split(b"\r\n\r\n", 1)[1]
        sock.settimeout(None)
        self.connected = True

        def read(n):
            nonlocal buf
            while len(buf) < n:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("websocket closed")
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out

        while True:
            b1, b2 = read(2)
            opcode, length = b1 & 0x0F, b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", read(8))[0]
            mask = read(4) if b2 & 0x80 else None
            payload = read(length)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
            if opcode == 8:
                raise ConnectionError("websocket closed")
            if opcode == 9:  # ping -> masked pong
                m = os.urandom(4)
                sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + m + bytes(c ^ m[i % 4] for i, c in enumerate(payload)))
            elif opcode == 1:
                try:
                    self.handle(json.loads(payload))
                except Exception:
                    pass

    def handle(self, msg):
        kind, data = msg.get("type"), msg.get("data") or {}
        pid = data.get("prompt_id")
        if not pid:
            return
        now = time.time()
        with self.mutex:
            st = self.state.setdefault(pid, {"started": None, "node": None, "node_since": None, "steps": [],
                                             "max": 0, "nodes_seen": {}, "done": False})
            if kind == "execution_start":
                st["started"] = now
            elif kind == "executing":
                node = data.get("node")
                if st["node"] is not None and st["node_since"]:
                    st["nodes_seen"][st["node"]] = now - st["node_since"]
                st["node"], st["node_since"] = node, now
                if node is None:
                    st["done"] = True
            elif kind == "progress":
                if data.get("node") in (N_SAMPLER, N_IMG_SAMPLER):
                    st["max"] = data.get("max", 0)
                    if not st["steps"] or st["steps"][-1][1] != data.get("value"):
                        st["steps"].append((now, data.get("value")))
            elif kind in ("execution_success", "execution_error", "execution_interrupted"):
                st["done"] = True
                st["finished"] = now

    def snapshot(self, pid):
        with self.mutex:
            st = self.state.get(pid)
            return copy.deepcopy(st) if st else None


MONITOR = Monitor()

# Defaults measured on an RTX 5090 at 1664x928, 124 frames, 6 low + 6 high SelfLift steps.
REF_WORK = 1664 * 928 * 124
DEFAULT_STATS = {"pre": 12.0, "low_step": 3.4, "high_step": 8.9, "transition": 1.6, "post": 16.0,
                 "rewrite": 25.0, "runs": 0}


def work_of(job):
    return max(1.0, job.get("width", 1280) * job.get("height", 720) * (float(job.get("seconds", 5)) * FPS + 1))


def scales(job):
    r = work_of(job) / REF_WORK
    return r ** 1.0, r ** 1.25  # linear parts (load/decode), attention-ish parts (sampling)


def sampler_steps(job):
    total = int(job.get("steps", 12))
    low = int(job.get("transition_step", 6))
    return low, total - low


def estimate(job, st, stats):
    """Returns (stage, percent 0-100, eta seconds or None)."""
    lin, att = scales(job)
    n_low, n_high = sampler_steps(job)
    pre = stats["pre"] * lin
    low_step, high_step = stats["low_step"] * att, stats["high_step"] * att
    post = stats["post"] * lin + (12 if job.get("extend") else 0)
    full = pre + n_low * low_step + stats["transition"] + n_high * high_step + post
    now = time.time()
    if not st or not st.get("started"):
        return "queued", 0, full
    elapsed = now - st["started"]
    steps = st["steps"]
    node = st.get("node")
    if not steps:
        if node in (N_DECODE, N_ADECODE) or (node and st["nodes_seen"].get(N_SAMPLER)):
            remain = max(2.0, post - (now - (st["node_since"] or now)))
            stage = "decode"
        else:
            remain = max(3.0, pre - elapsed) + n_low * low_step + stats["transition"] + n_high * high_step + post
            stage = "prepare"
    else:
        v = steps[-1][1]
        durs = [steps[i][0] - steps[i - 1][0] for i in range(1, len(steps))]
        low_durs = [d for (t, s), d in zip(steps[1:], durs) if s <= n_low]
        high_durs = [d for (t, s), d in zip(steps[1:], durs) if s > n_low + 1]
        if low_durs:
            low_step = sorted(low_durs)[len(low_durs) // 2]
            high_step = low_step * stats["high_step"] / stats["low_step"]
        if high_durs:
            high_step = sorted(high_durs)[len(high_durs) // 2]
        since = now - steps[-1][0]
        if node == N_SAMPLER and v < n_low + n_high:
            stage = "low" if v < n_low else "high"
            if v < n_low:
                remain = max(0.0, (n_low - v) * low_step - since) + stats["transition"] + n_high * high_step + post
            else:
                remain = max(0.0, (n_low + n_high - v) * high_step - since) + post
        else:
            stage = "decode"
            remain = max(2.0, post - (now - (st["node_since"] or now)))
    percent = max(1, min(99, int(100 * elapsed / (elapsed + remain))))
    return stage, percent, remain


def learn(job, st):
    """Fold a finished job's measured stage times into stats.json (exponential moving average)."""
    steps = st.get("steps") or []
    if not st.get("started") or len(steps) < 3:
        return
    lin, att = scales(job)
    n_low, n_high = sampler_steps(job)
    first_step = steps[0][0]
    durs = [(steps[i][1], steps[i][0] - steps[i - 1][0]) for i in range(1, len(steps))]
    low = [d for s, d in durs if s <= n_low]
    high = [d for s, d in durs if s > n_low + 1]
    end = st.get("finished") or time.time()
    sample = {"pre": max(0.0, first_step - st["started"]) / lin, "post": max(0.0, end - steps[-1][0]) / lin}
    if low:
        sample["low_step"] = sorted(low)[len(low) // 2] / att
    if high:
        sample["high_step"] = sorted(high)[len(high) // 2] / att
    if job.get("extend"):
        sample["post"] = max(0.0, sample["post"] - 12 / lin)
    with lock:
        stats = load_json(STATS_PATH, DEFAULT_STATS)
        for k, v in sample.items():
            stats[k] = round(stats[k] * 0.6 + v * 0.4, 3) if stats.get("runs") else round(v, 3)
        stats["runs"] = stats.get("runs", 0) + 1
        save_json(STATS_PATH, stats)


# ---------------------------------------------------------------- asset library

def library_index():
    return load_json(LIBRARY / "library.json", [])


def probe_video(path):
    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else float(c.duration or 0) / av.time_base
        fps = float(stream.average_rate or 0)
        return duration, fps, len(c.streams.audio) > 0, stream.codec_context.width, stream.codec_context.height


def video_frames(path, count):
    """Sample frames uniformly like post_vllm.sample_video; returns [(timestamp, PIL.Image)]."""
    duration, fps, *_ = probe_video(path)
    last = max(0.0, duration - max(0.1, 1 / fps if fps > 0 else 0.1))
    if fps > 0:
        count = max(1, min(count, round(duration * fps)))
    stamps = [round(last * i / (count - 1), 6) for i in range(count)] if count > 1 else [0.0]
    out = []
    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        for ts in stamps:
            c.seek(int(ts / stream.time_base), stream=stream, backward=True)
            frame = None
            for f in c.decode(stream):
                frame = f
                if f.time is not None and f.time >= ts:
                    break
            if frame is not None:
                out.append((ts, frame.to_image()))
    return duration, out


def last_frame(path):
    """The final decodable frame of a video."""
    frame = None
    with av.open(str(path)) as c:
        stream = c.streams.video[0]
        duration = probe_video(path)[0]
        c.seek(int(max(0.0, duration - 1.5) / stream.time_base), stream=stream, backward=True)
        for f in c.decode(stream):
            frame = f
    if frame is None:
        raise ValueError("讀不到延伸影片的最後一格")
    return frame.to_image()


def add_asset(cfg, filename, body, folder=None):
    ext = Path(filename).suffix.lower()
    kind = "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else "audio" if ext in AUDIO_EXT else None
    if kind is None:
        raise ValueError(f"不支援的檔案格式：{ext}")
    asset_id = uuid.uuid4().hex[:10]
    LIBRARY.mkdir(exist_ok=True)
    stored = LIBRARY / f"{asset_id}{ext}"
    stored.write_bytes(body)
    item = {"id": asset_id, "name": Path(filename).name, "kind": kind, "file": stored.name,
            "comfy_name": f"h3fe_{asset_id}{ext}", "created": time.time(), "folder": folder or None}
    thumb = LIBRARY / f"{asset_id}_thumb.jpg"
    if kind == "image":
        with Image.open(stored) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            item["width"], item["height"] = im.size
            im.thumbnail((360, 360))
            im.save(thumb, quality=85)
        item["thumb"] = thumb.name
    elif kind == "video":
        duration, fps, has_audio, w, h = probe_video(stored)
        item.update(duration=round(duration, 2), fps=round(fps, 2), has_audio=has_audio, width=w, height=h)
        frames = video_frames(stored, 1)[1]
        if frames:
            im = frames[0][1].convert("RGB")
            im.thumbnail((360, 360))
            im.save(thumb, quality=85)
            item["thumb"] = thumb.name
    shutil.copyfile(stored, Path(cfg["comfy_input_dir"]) / item["comfy_name"])
    with lock:
        index = library_index()
        index.insert(0, item)
        save_json(LIBRARY / "library.json", index)
    return item


def add_output_to_library(cfg, job_id, folder=None):
    job = next((j for j in load_json(JOBS_PATH, []) if j["id"] == job_id), None)
    if not job or job.get("status") != "done":
        raise ValueError("這個工作還沒有完成的影片")
    if job.get("image"):
        # 🎬 變成影片 and ＋加入素材庫 on the same picture must not stack up copies in the library
        known = next((i for i in library_index() if i.get("from_job") == job_id), None)
        if known:
            return known
        tmp = OUTPUTS / f"_lib_{job_id}.png"
        comfy_download(cfg, job["image"], tmp)
        body = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        item = add_asset(cfg, job["image"]["filename"], body, folder)
        with lock:
            index = library_index()
            for i in index:
                if i["id"] == item["id"]:
                    i["from_job"] = job_id
            save_json(LIBRARY / "library.json", index)
        return dict(item, from_job=job_id)
    if job.get("full_video"):
        body, name = (OUTPUTS / job["full_video"]).read_bytes(), job["full_video"]
    else:
        v = job["video"]
        tmp = OUTPUTS / f"_lib_{job_id}.mp4"
        comfy_download(cfg, v, tmp)
        body, name = tmp.read_bytes(), v["filename"]
        tmp.unlink(missing_ok=True)
    return add_asset(cfg, name, body, folder)


def delete_asset(cfg, asset_id):
    with lock:
        index = library_index()
        item = next((i for i in index if i["id"] == asset_id), None)
        if item is None:
            return
        index.remove(item)
        save_json(LIBRARY / "library.json", index)
    for name in (item["file"], item.get("thumb")):
        if name:
            (LIBRARY / name).unlink(missing_ok=True)
    (Path(cfg["comfy_input_dir"]) / item["comfy_name"]).unlink(missing_ok=True)


def folder_action(b):
    """Create / rename / delete a library folder. Deleting a folder moves its assets back to the top level."""
    with lock:
        folders = load_json(FOLDERS_PATH, [])
        act = b.get("action")
        if act == "create":
            name = (b.get("name") or "").strip() or "新資料夾"
            folders.append({"id": uuid.uuid4().hex[:8], "name": name[:40], "created": time.time()})
        elif act == "rename":
            for f in folders:
                if f["id"] == b.get("id"):
                    f["name"] = ((b.get("name") or "").strip() or f["name"])[:40]
        elif act == "delete":
            folders = [f for f in folders if f["id"] != b.get("id")]
            index = library_index()
            for item in index:
                if item.get("folder") == b.get("id"):
                    item["folder"] = None
            save_json(LIBRARY / "library.json", index)
        else:
            raise ValueError("未知的資料夾動作")
        save_json(FOLDERS_PATH, folders)
        return folders


def move_assets(ids, folder):
    with lock:
        if folder and folder not in {f["id"] for f in load_json(FOLDERS_PATH, [])}:
            raise ValueError("資料夾不存在")
        index = library_index()
        for item in index:
            if item["id"] in ids:
                item["folder"] = folder or None
        save_json(LIBRARY / "library.json", index)
        return index


# ---------------------------------------------------------------- references

def asset(asset_id):
    item = next((i for i in library_index() if i["id"] == asset_id), None)
    if item is None:
        raise ValueError("素材已經不在素材庫裡了")
    return item


def resolve_mentions(text, tray):
    """Map @圖片N mentions (N = position among tray items of that kind) to compact H3 tags.

    The extension source video is not a reference, so it is skipped when numbering.
    Only mentioned assets are used, numbered by first mention, like H3Easy's per-segment binding.
    Returns (text_with_tags, {"image": [...], "video": [...], "audio": [...]}).
    """
    index = {i["id"]: i for i in library_index()}
    by_kind = {"image": [], "video": [], "audio": []}
    for entry in tray:
        item = index.get(entry["id"])
        if item and not entry.get("extend"):
            by_kind[item["kind"]].append(dict(item, use_audio=entry.get("use_audio", True)))
    chosen = {"image": [], "video": [], "audio": []}
    assigned = {}

    def repl(m):
        kind = MENTION_KIND[m.group(1).lower() if m.group(1).isascii() else m.group(1)]
        n = int(m.group(2))
        pool = by_kind[kind]
        if not 1 <= n <= len(pool):
            raise ValueError(f"找不到參考素材 {m.group(0)}")
        key = (kind, n)
        if key not in assigned:
            chosen[kind].append(pool[n - 1])
            assigned[key] = len(chosen[kind])
        return f"<{LABEL[kind]} {assigned[key]}>"

    return MENTION_RE.sub(repl, text), chosen


def extend_source(req):
    """The tray video marked for extension (ref mode only), or None."""
    if req.get("mode", "ref") != "ref":
        return None
    marked = [t for t in req.get("tray", []) if t.get("extend")]
    if len(marked) > 1:
        raise ValueError("一次只能標記一支影片作為延伸")
    if not marked:
        return None
    item = asset(marked[0]["id"])
    if item["kind"] != "video":
        raise ValueError("只有影片可以標記為延伸")
    return item


def extend_last_frame(cfg, src):
    """Save the source's last frame into ComfyUI's input folder; the new clip continues from it."""
    name = f"h3t_last_{src['id']}.jpg"
    path = Path(cfg["comfy_input_dir"]) / name
    if not path.exists():
        last_frame(LIBRARY / src["file"]).convert("RGB").save(path, quality=95)
    return {"id": f"last_{src['id']}", "name": "延伸來源最後一格", "kind": "image", "comfy_name": name,
            "path": str(path), "extend_frame": True}


def collect(cfg, req, text):
    """Resolve the request into (tagged_text, chosen media, task, extend source)."""
    mode = req.get("mode", "ref")
    if mode == "ref":
        tagged, chosen = resolve_mentions(text, req.get("tray", []))
        src = extend_source(req)
        if src:
            chosen["image"].append(extend_last_frame(cfg, src))
        return tagged, chosen, "Ref2VA", src
    chosen = {"image": [], "video": [], "audio": []}
    if mode == "frames":
        first, last = req.get("first_id"), req.get("last_id")
        if not first and not last:
            raise ValueError("頭尾格模式至少要放一張首格或尾格")
        for asset_id in (first, last):
            if asset_id:
                item = asset(asset_id)
                if item["kind"] != "image":
                    raise ValueError("首格與尾格必須是圖片")
                chosen["image"].append(item)
        task = "FL2VA" if first and last else "I2VA" if first else "L2VA"
        return text, chosen, task, None
    return text, chosen, "T2VA", None


def media_path(item):
    return Path(item["path"]) if item.get("path") else LIBRARY / item["file"]


# ---------------------------------------------------------------- prompt rewriting

def load_skills(cfg):
    skills = []
    base = Path(cfg["skills_dir"])
    if base.is_dir():
        for d in sorted(base.iterdir()):
            if (d / "SKILL.md").is_file():
                skills.append({"id": d.name, "title": SKILL_NAMES.get(d.name, d.name)})
    return skills


def skill_text(cfg, skill_id):
    if not skill_id:
        return ""
    path = Path(cfg["skills_dir"]) / skill_id / "SKILL.md"
    if path.parent.parent != Path(cfg["skills_dir"]) or not path.is_file():
        raise ValueError(f"找不到導演 Skill：{skill_id}")
    return re.sub(r"^---\n.*?\n---\n", "", path.read_text(encoding="utf-8"), flags=re.S).strip()


def pad_square(im, side):
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((side, side))
    canvas = Image.new("RGB", (side, side), "white")
    canvas.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
    return canvas


def alignment_line(task, duration, last_shot=1):
    """The fixed first line the base guide requires for keyframe tasks (section 2.1)."""
    end = f"{duration:.2f}"
    if task == "I2VA":
        return "For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
    if task == "FL2VA":
        return ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                f"0.00-second mark of the target video; Picture 2 (from Shot {last_shot}) aligns with the "
                f"{end}-second mark of the target video.")
    if task == "L2VA":
        return ("How the reference pictures align with the target video — <Picture 1> (from "
                f"[Shot {last_shot}]) aligns with the {end}-second mark of the target video.")
    return ""


BASE_SECTIONS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")
REF_SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description") + BASE_SECTIONS[1:]


def normalize_base(text, task, duration):
    """Bring the rewriter's answer into the base guide's format.

    The rewriter was trained on Ref2VA, so for T2VA/I2VA/FL2VA/L2VA it sometimes answers in the
    six Ref2VA sections. Keep its description, map <Subject N> back to plain words, and rebuild the
    alignment line, which is formulaic anyway.
    """
    text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
    keys = "|".join(REF_SECTIONS + BASE_SECTIONS[:1])
    parts = {}
    for m in re.finditer(rf"^\s*({keys})\s*:\s*(.*?)(?=^\s*(?:{keys})\s*:|\Z)", text, flags=re.S | re.M):
        parts[m.group(1)] = m.group(2).strip()
    body = parts.get("integrated_multimodal_description") or parts.get("detailed_description")
    if not body:
        body = text
        for line in text.splitlines():  # drop a stray alignment line; it is rebuilt below
            if line.startswith(("How the reference pictures", "For the target video")):
                body = text.replace(line, "", 1).strip()
    subjects = {}
    for m in re.finditer(r"<Subject (\d+)>\s+is\s+(.+)", parts.get("subject_definitions", "")):
        subjects[m.group(1)] = m.group(2).split(",")[0].strip().rstrip(".")
    body = re.sub(r"<Subject (\d+)>", lambda m: subjects.get(m.group(1), "the character"), body)
    shots = [int(n) for n in re.findall(r"\[Shot (\d+)\]", body)]
    out = []
    head = alignment_line(task, duration, max(shots) if shots else 1)
    if head:
        out += [head, ""]
    out.append("integrated_multimodal_description: " + body)
    out.append("\noverall_soundscape: " + (parts.get("overall_soundscape") or "N/A"))
    out.append("\nnon_diegetic_music: " + (parts.get("non_diegetic_music") or "N/A"))
    return "\n".join(out)


def raw_prompt(text, task, duration, chosen, src):
    """Final prompt when the rewriter is off."""
    if task == "Ref2VA":
        if src:
            k = len(chosen["image"])
            text += (f"\n<Picture {k}> is the last frame of the previous clip; the video continues directly from it.")
        return text
    head = alignment_line(task, duration)
    return (head + "\n\n" if head else "") + text


def rewrite_message(cfg, tagged_text, chosen, task, duration, skill_id, src):
    """The rewriter's input as (system text, user parts), parts = [("text", str) | ("image", PIL.Image)].

    Order and wording follow post_vllm.build_messages, so every backend sees the same message.
    """
    rw = cfg["rewriter"]
    side = int(rw["image_side"])
    names = ({"Subject": "主体", "Picture": "图片", "Video": "视频", "Audio": "音频"}
             if re.search(r"[㐀-鿿]", tagged_text) else
             {"Subject": "subject ", "Picture": "image ", "Video": "video ", "Audio": "audio "})
    original = re.sub(r"<(Subject|Picture|Video|Audio)\s+(\d+)>", lambda m: names[m[1]] + m[2], tagged_text.strip())

    text = f"Target video duration: {duration:.6f} seconds.\n"
    if task == "Ref2VA":
        if chosen["image"]:
            text += "Inspect the reference images labeled <Picture N> and use them according to the user's instructions.\n"
        if src:
            k = len(chosen["image"])
            text += (f"<Picture {k}> is the last frame of the previous clip. The target video is its direct continuation: "
                     f"it starts exactly from <Picture {k}> and carries the same scene, characters and motion forward.\n")
        if chosen["video"]:
            text += "Video frames are sparse samples of their labeled <Video N>; timestamps refer to source time, not target cuts.\n"
        if chosen["audio"]:
            text += ("The user has also provided reference audio, but you do not know its actual content and should not guess. "
                     "Enhance the prompt according to the user's instructions.\n")
    else:
        text += f"Task: {task}.\n"
        if task == "I2VA":
            text += "<Picture 1> is the actual first frame of the video at 0.00 seconds.\n"
        elif task == "FL2VA":
            text += f"<Picture 1> is the first frame at 0.00 seconds and <Picture 2> is the last frame at {duration:.2f} seconds.\n"
        elif task == "L2VA":
            text += f"<Picture 1> is the final frame of the video at {duration:.2f} seconds.\n"
    text += ("Choose shots that best express the original intent. "
             "Allocate shot durations and cut timing within the target duration according to content and pacing. "
             "Respect explicit shot requests, reference constraints and the guide's transition rules.\n")
    skill = skill_text(cfg, skill_id)
    if skill:
        text += ("Directing method for choreography and shot planning only; it never changes the guide's "
                 f"output format, reference labels, dialogue or duration:\n{skill}\n")
    text += f"Original prompt:\n{original}\n"

    parts = []
    for kind in ("image", "video", "audio"):
        for i, item in enumerate(chosen[kind], start=1):
            label = f"<{LABEL[kind]} {i}>"
            text += f"{CHINESE_NAME[kind]}{i} / reference {kind} {i}: {label}."
            if kind == "image":
                with Image.open(media_path(item)) as im:
                    parts += [("text", text), ("image", pad_square(im, side))]
                text = ""
            elif kind == "video":
                src_duration, frames = video_frames(media_path(item), int(rw["video_frames"]))
                text += f"{label} duration: {src_duration:.6f}s. Frames follow in source order."
                for ts, im in frames:
                    text += f"{label} frame near source time {ts:.6f}s."
                    parts += [("text", text), ("image", pad_square(im, side))]
                    text = ""
    if text:
        parts.append(("text", text))

    if task == "Ref2VA":
        system = SYSTEM_REF + Path(cfg["guides"]["ref"]).read_text(encoding="utf-8")
    else:
        system = SYSTEM_BASE.format(task=task) + Path(cfg["guides"]["base"]).read_text(encoding="utf-8")
    return system.strip(), parts


def jpeg_b64(im):
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def rewrite_local(cfg, system, parts, job_id):
    """ComfyUI TextGenerate with the quantized H3 rewriter: raw Qwen3.5 chat text + vision images."""
    rw = cfg["rewriter"]
    body = "".join(p if kind == "text" else VISION for kind, p in parts)
    raw = (f"<|im_start|>system\n{system}<|im_end|>\n"
           f"<|im_start|>user\n{body.strip()}<|im_end|>\n<|im_start|>assistant\n")
    raw += "<think>\n" if rw["thinking"] else "<think>\n\n</think>\n\n"
    inp = Path(cfg["comfy_input_dir"])
    wf = {"1": {"class_type": "CLIPLoader", "inputs": {"clip_name": rw["model"], "type": "stable_diffusion", "device": "default"}}}
    batch, names = None, []
    for n, im in enumerate(p for kind, p in parts if kind == "image"):
        name = f"h3fe_rw_{job_id}_{n}.jpg"
        im.save(inp / name, quality=92)
        names.append(name)
        wf[f"i{n}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        if batch is None:
            batch = [f"i{n}", 0]
        else:
            wf[f"b{n}"] = {"class_type": "ImageBatch", "inputs": {"image1": batch, "image2": [f"i{n}", 0]}}
            batch = [f"b{n}", 0]
    gen = {"clip": ["1", 0], "prompt": raw, "max_length": int(rw["max_length"]),
           "sampling_mode": "on", "sampling_mode.temperature": float(rw["temperature"]),
           "sampling_mode.top_k": int(rw["top_k"]), "sampling_mode.top_p": float(rw["top_p"]),
           "sampling_mode.min_p": 0.0, "sampling_mode.repetition_penalty": 1.0,
           "sampling_mode.seed": random.randint(0, 2**31 - 1), "sampling_mode.presence_penalty": 0.0,
           "thinking": bool(rw["thinking"]), "use_default_template": True, "mtp": rw["mtp"]}
    if batch:
        gen["image"] = batch
    wf["2"] = {"class_type": "TextGenerate", "inputs": gen}
    wf["3"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    try:
        entry = wait_prompt(cfg, queue_prompt(cfg, wf))
    finally:
        for name in names:
            (inp / name).unlink(missing_ok=True)
    if cfg.get("free_after_rewrite"):
        try:
            comfy(cfg, "/free", {"unload_models": True, "free_memory": True})
        except Exception:
            pass
    return "".join(entry["outputs"].get("3", {}).get("text", [])).strip()


def http_json(url, payload=None, headers=None, timeout=600, retries=2):
    """JSON request; transient server errors (500/502/503, common on free Gemini/Gemma tiers) are retried."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (500, 502, 503) and retries > 0:
            time.sleep(3)
            return http_json(url, payload, headers, timeout, retries - 1)
        detail = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(detail)
            if isinstance(parsed, list) and parsed:  # Google's OpenAI-compatible endpoint wraps errors in a list
                parsed = parsed[0]
            err = parsed.get("error", detail)
            detail = err.get("message", err) if isinstance(err, dict) else err
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"HTTP {e.code}：{str(detail)[:600]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"連不上端點：{e.reason}")


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def rewrite_gemini(gm, rw, system, parts, max_tokens=None):
    if not gm.get("api_key"):
        raise ValueError("還沒有填 Gemini API 金鑰")
    model = (gm.get("model") or "gemini-3.8-flash").strip()
    contents = [{"text": p} if kind == "text" else {"inline_data": {"mime_type": "image/jpeg", "data": jpeg_b64(p)}}
                for kind, p in parts]
    payload = {"contents": [{"role": "user", "parts": contents}],
               "generationConfig": {"temperature": float(rw["temperature"]), "topP": float(rw["top_p"]),
                                    "maxOutputTokens": int(max_tokens or rw["max_length"])}}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    out = http_json(f"{GEMINI_BASE}/models/{urllib.parse.quote(model)}:generateContent", payload,
                    {"x-goog-api-key": gm["api_key"]})
    try:
        return "".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"] if not p.get("thought")).strip()
    except (KeyError, IndexError):
        raise RuntimeError("Gemini 沒有回傳內容：" + json.dumps(out, ensure_ascii=False)[:400])


def openai_url(base):
    base = (base or "").strip().rstrip("/")
    if not base:
        raise ValueError("還沒有填 OpenAI 相容端點網址")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def rewrite_openai(oa, rw, system, parts, max_tokens=None):
    content = [{"type": "text", "text": p} if kind == "text" else
               {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + jpeg_b64(p)}} for kind, p in parts]
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    payload = {"model": (oa.get("model") or "").strip() or "default", "messages": messages,
               "temperature": float(rw["temperature"]), "top_p": float(rw["top_p"]),
               "max_tokens": int(max_tokens or rw["max_length"])}
    headers = {"Authorization": "Bearer " + oa["api_key"]} if oa.get("api_key") else {}
    out = http_json(openai_url(oa.get("base_url")), payload, headers)
    try:
        text = out["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("端點沒有回傳內容：" + json.dumps(out, ensure_ascii=False)[:400])
    if isinstance(text, list):
        text = "".join(c.get("text", "") for c in text)
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def rewrite(cfg, req):
    tagged, chosen, task, src = collect(cfg, req, req["prompt"])
    duration = float(req["seconds"])
    system, parts = rewrite_message(cfg, tagged, chosen, task, duration, req.get("skill"), src)
    rw = cfg["rewriter"]
    provider = rw.get("provider", "local")
    t0 = time.time()
    if provider == "gemini":
        text = rewrite_gemini(rw["gemini"], rw, system, parts)
    elif provider == "openai":
        text = rewrite_openai(rw["openai"], rw, system, parts)
    else:
        text = rewrite_local(cfg, system, parts, uuid.uuid4().hex[:8])
    text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
    if task != "Ref2VA":
        text = normalize_base(text, task, duration)
    used = round(time.time() - t0, 1)
    with lock:
        stats = load_json(STATS_PATH, DEFAULT_STATS)
        stats["rewrite"] = round(stats["rewrite"] * 0.6 + used * 0.4, 1)
        save_json(STATS_PATH, stats)
    return {"prompt": text, "seconds_used": used, "task": task, "provider": provider}


def test_rewriter(cfg, body):
    """Settings' 驗證 button: a tiny POST to the chosen endpoint, plus its model list when it offers one.

    An empty api_key in the form means "keep the stored key" (the browser never receives stored keys).
    """
    provider = body.get("provider")
    if provider not in ("gemini", "openai"):
        raise ValueError("本地小模型不需要驗證")
    conf = dict(cfg["rewriter"].get(provider, {}))
    conf.update({k: v for k, v in (body.get("conf") or {}).items() if v not in ("", None)})
    rw = dict(cfg["rewriter"], temperature=0.2, top_p=0.9)
    ping = [("text", "Reply with the single word OK.")]
    t0 = time.time()
    models = []
    if provider == "gemini":
        reply = rewrite_gemini(conf, rw, "", ping, max_tokens=512)
        try:
            listed = http_json(f"{GEMINI_BASE}/models?pageSize=200", headers={"x-goog-api-key": conf["api_key"]}, timeout=30)
            models = [m["name"].split("/", 1)[-1] for m in listed.get("models", [])
                      if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in m["name"]]
        except Exception:
            pass
    else:
        reply = rewrite_openai(conf, rw, "", ping, max_tokens=512)
        try:
            base = openai_url(conf.get("base_url")).rsplit("/chat/completions", 1)[0]
            headers = {"Authorization": "Bearer " + conf["api_key"]} if conf.get("api_key") else {}
            models = [m["id"] for m in http_json(base + "/models", headers=headers, timeout=20).get("data", [])]
        except Exception:
            pass
    return {"ok": True, "reply": reply[:80], "ms": int((time.time() - t0) * 1000), "models": models}


# ---------------------------------------------------------------- video generation

def round32(x):
    return max(32, int(round(x / 32)) * 32)


def dims(aspect, resolution, auto_size=None):
    """aspect "auto" follows auto_size (first frame / extension source) and falls back to 16:9."""
    short = int(resolution.rstrip("pP"))
    if aspect == "auto" and auto_size:
        a, b = auto_size
    else:
        a, b = ASPECTS.get(aspect, (16, 9))
    w, h = (short * a / b, short) if a >= b else (short, short * b / a)
    return round32(w), round32(h)


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("找不到 ffmpeg：延伸影片需要 ffmpeg")


def conform(src_path, dest, width, height, has_audio, tail=None):
    """Re-encode (the tail of) a video at 24 fps and the generation size, always with a stereo track."""
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error"]
    if tail:
        cmd += ["-sseof", f"-{tail}"]
    cmd += ["-i", str(src_path)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-vf", f"fps={FPS},scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
            "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-shortest",
            "-c:v", "libx264", "-crf", "12", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def join_clips(first, second, dest):
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(first), "-i", str(second), "-filter_complex",
           "[0:a]aresample=48000,aformat=channel_layouts=stereo[a0];[1:a]aresample=48000,aformat=channel_layouts=stereo[a1];"
           "[0:v][a0][1:v][a1]concat=n=2:v=1:a=1[v][a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "17", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)


def bypass(wf, node_id, port="model"):
    """Remove a pass-through node and wire its consumers to its input."""
    upstream = wf[node_id]["inputs"][port]
    for other in wf.values():
        for k, v in other["inputs"].items():
            if v == [node_id, 0]:
                other["inputs"][k] = upstream
    del wf[node_id]


def build_generate(cfg, final_text, chosen, task, req, size, src_job=None):
    wf = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    # preview-only nodes are not needed behind a web frontend
    for node_id in ("329", "401"):
        if node_id in wf:
            bypass(wf, node_id)
    for node_id in PREVIEW_ONLY:
        wf.pop(node_id, None)
    width, height = size

    if task == "Ref2VA":
        ref = wf[N_COND]["inputs"]
        for k in [k for k in ref if k.startswith("ref_") and "." in k]:  # autogrow slots only
            del ref[k]
        for i, item in enumerate(chosen["image"]):
            wf[f"ri{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["comfy_name"]}}
            ref[f"ref_images.ref_image_{i}"] = [f"ri{i}", 0]
        for i, item in enumerate(chosen["video"]):
            wf[f"rv{i}"] = {"class_type": "LoadVideo", "inputs": {"file": item["comfy_name"]}}
            wf[f"rc{i}"] = {"class_type": "GetVideoComponents", "inputs": {"video": [f"rv{i}", 0]}}
            ref[f"ref_videos.ref_video_{i}"] = [f"rc{i}", 0]
            if item.get("has_audio") and item.get("use_audio", True):
                ref[f"ref_video_audios.ref_video_audio_{i}"] = [f"rc{i}", 1]
        for i, item in enumerate(chosen["audio"]):
            wf[f"ra{i}"] = {"class_type": "LoadAudio", "inputs": {"audio": item["comfy_name"]}}
            ref[f"ref_audios.ref_audio_{i}"] = [f"ra{i}", 0]
        ref["prompt"] = final_text
        ref["width"], ref["height"] = width, height
    else:  # T2VA / I2VA / FL2VA / L2VA share the stock image-to-video conditioning
        old = wf[N_COND]["inputs"]
        node = {"clip": [N_CLIP, 0], "vae": [N_VAE, 0], "prompt": final_text, "width": width, "height": height,
                "length": old["length"]}
        frames = chosen["image"]
        slots = {"I2VA": ["first_frame"], "FL2VA": ["first_frame", "last_frame"], "L2VA": ["last_frame"]}.get(task, [])
        for i, (slot, item) in enumerate(zip(slots, frames)):
            wf[f"kf{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["comfy_name"]}}
            wf[f"ks{i}"] = {"class_type": "ImageScale", "inputs": {"image": [f"kf{i}", 0], "upscale_method": "lanczos",
                                                                    "width": width, "height": height, "crop": "center"}}
            node[slot] = [f"ks{i}", 0]
        wf[N_COND] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": node}

    wf[N_SECONDS]["inputs"]["value"] = float(req["seconds"])
    total, low = cfg["steps"]["fast" if req.get("fast", True) else "normal"]
    wf[N_SCHED]["inputs"]["steps"], wf[N_SAMPLER]["inputs"]["transition_step"] = int(total), int(low)
    seed = req.get("seed")
    wf[N_SAMPLER]["inputs"]["seed"] = int(seed) if seed not in (None, "", -1) else random.randint(0, 2**48)

    images, audio = [N_DECODE, 0], [N_ADECODE, 0]
    if src_job:  # pin the source tail into the head of the clip, then cut the pinned frames off again
        ext = cfg["extend"]
        wf["ev"] = {"class_type": "LoadVideo", "inputs": {"file": src_job["tail_name"]}}
        wf["ec"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["ev", 0]}}
        wf["mc"] = {"class_type": "MiniMaxH3MotionContext", "inputs": {
            "conditioning": [N_BRIDGE, 0], "vae": [N_VAE, 0], "latent": [N_COND, 1],
            "context_length": str(ext["context_length"]), "audio_context_length": int(ext["audio_context_length"]),
            "context_frames": ["ec", 0], "audio_vae": [N_AVAE, 0], "context_audio": ["ec", 1]}}
        wf[N_SAMPLER]["inputs"]["positive"] = ["mc", 0]
        wf["mt"] = {"class_type": "MiniMaxH3MotionContextTrim", "inputs": {
            "images": images, "trim_frames": ["mc", 1], "audio": audio, "fps": float(FPS), "match_tail": True}}
        images, audio = ["mt", 0], ["mt", 1]

    wf["cv"] = {"class_type": "CreateVideo", "inputs": {"images": images, "audio": audio, "fps": float(FPS)}}
    wf["sv"] = {"class_type": "SaveVideo", "inputs": {"video": ["cv", 0], "filename_prefix": "H3Toybox/h3t",
                                                      "format": "mp4", "format.codec": "h264"}}

    intensity = req.get("intensity", "off")
    mult = cfg["intensity_multipliers"].get(intensity)
    for node_id in cfg["loras"]["action"]:
        node = wf.get(node_id)
        if node is None:
            continue
        if mult is None:  # intensity off: bypass by rewiring consumers to this LoRA's input model
            bypass(wf, node_id)
        else:
            node["inputs"]["strength_model"] = round(node["inputs"]["strength_model"] * mult, 4)
    return wf, wf[N_SAMPLER]["inputs"]["seed"]


def generate(cfg, req):
    mode = req.get("mode", "ref")
    if mode not in ("ref", "frames", "text"):
        raise ValueError("未知的模式")
    duration = float(req["seconds"])
    tagged, chosen, task, src = collect(cfg, req, req["prompt"])
    final_text = req.get("final_prompt") or raw_prompt(tagged, task, duration, chosen, src)

    auto_size = None
    if src:
        auto_size = (src["width"], src["height"])
    elif task in ("I2VA", "FL2VA", "L2VA"):
        first = chosen["image"][0]
        auto_size = (first.get("width"), first.get("height")) if first.get("width") else None
    size = dims(req["aspect"], req["resolution"], auto_size)

    job_id = uuid.uuid4().hex[:12]
    src_job = None
    if src:
        tail = f"h3t_tail_{job_id}.mp4"
        conform(LIBRARY / src["file"], Path(cfg["comfy_input_dir"]) / tail, *size, src.get("has_audio"),
                tail=cfg["extend"]["tail_seconds"])
        src_job = {"tail_name": tail}
    wf, seed = build_generate(cfg, final_text, chosen, task, req, size, src_job)
    prompt_id = queue_prompt(cfg, wf)
    job = {"id": prompt_id, "created": time.time(), "status": "queued", "mode": mode, "task": task,
           "prompt": req["prompt"], "final_prompt": final_text, "seed": seed, "aspect": req["aspect"],
           "resolution": req["resolution"], "width": size[0], "height": size[1], "seconds": req["seconds"],
           "intensity": req.get("intensity", "off"), "skill": req.get("skill") or "", "tray": req.get("tray", []),
           "first_id": req.get("first_id"), "last_id": req.get("last_id"),
           "fast": bool(req.get("fast", True)), "steps": wf[N_SCHED]["inputs"]["steps"],
           "transition_step": wf[N_SAMPLER]["inputs"]["transition_step"]}
    if src:
        job["extend"] = {"source_id": src["id"], "source_name": src["name"], "tail": src_job["tail_name"],
                         "has_audio": src.get("has_audio", False)}
    with lock:
        jobs = load_json(JOBS_PATH, [])
        jobs.insert(0, job)
        save_json(JOBS_PATH, jobs)
    return job


# ---------------------------------------------------------------- 🖼 picture generation (Qwen-Image 2.1)

def image_dims(aspect, size_key, refs):
    """Picture size from the long-side button; aspect "auto" follows the first reference (1:1 without one)."""
    long_side = IMAGE_SIZES.get(size_key, 1344)
    if aspect == "auto":
        a, b = (refs[0]["width"], refs[0]["height"]) if refs and refs[0].get("width") else (1, 1)
    else:
        a, b = ASPECTS.get(aspect, (1, 1))
    w, h = (long_side, long_side * b / a) if a >= b else (long_side * a / b, long_side)
    return round32(w), round32(h)


def image_refs(req, text):
    """Reference pictures for Qwen and the prompt with @圖片N turned into Qwen's own tags.

    Qwen's official edit spec: with two or more inputs the prompt must name them <image1>, <image2> ...;
    with a single input it must not tag it at all. Without any @ mention every tray picture is used, in order."""
    index = {i["id"]: i for i in library_index()}
    tray = [t for t in req.get("tray", []) if index.get(t["id"], {}).get("kind") == "image"]
    if MENTION_RE.search(text):
        tagged, chosen = resolve_mentions(text, tray)
        refs = chosen["image"]
    else:
        tagged, refs = text, [index[t["id"]] for t in tray]
    if len(refs) > 9:
        raise ValueError("參考圖最多 9 張")
    if ph3_mode(load_config()):  # P-H3 reads <Picture N> itself, up to 8 pictures (image_1 = the one edited)
        if len(refs) > 8:
            raise ValueError("P-H3 編輯最多 8 張圖（第 1 張是要改的那張）")
        return tagged, refs
    if len(refs) == 1:
        word = "图片" if re.search(r"[㐀-鿿]", tagged) else "the image"
        tagged = re.sub(r"<Picture\s+1>", word, tagged)
    else:
        tagged = re.sub(r"<Picture\s+(\d+)>", r"<image\1>", tagged)
    return tagged, refs


def ph3_mode(cfg):
    return cfg["image"].get("engine", "qwen") == "ph3"


def ph3_graph(cfg, prompt, w, h, refs, seed):
    """PrunaAI's p-h3-image_lora_* workflows: the LoRA bypass-loaded on the H3 model, SigmaShift 1/1, the 8-step
    grid, BasicGuider, euler. Same node ids as the Qwen graph for the encoder, sampler and saver."""
    p = cfg["image"]["ph3"]
    g = {"iu": {"class_type": "UNETLoader", "inputs": {"unet_name": p["unet"], "weight_dtype": "default"}},
         "ilo": {"class_type": "LoraLoaderBypassModelOnly", "inputs": {"model": ["iu", 0], "lora_name": p["lora"],
                                                                       "strength_model": 1.0}},
         "ish": {"class_type": "MiniMaxH3SigmaShift", "inputs": {"model": ["ilo", 0], "shift_video": 1.0, "shift_audio": 1.0}},
         "il": {"class_type": "CLIPLoader", "inputs": {"clip_name": p["clip"], "type": "minimax", "device": "default"}},
         "iv": {"class_type": "VAELoader", "inputs": {"vae_name": p["vae"]}},
         N_IMG_ENC: {"class_type": "PH3ImageConditioning", "inputs": {"clip": ["il", 0], "vae": ["iv", 0], "prompt": prompt,
                                                                      "width": w, "height": h}},
         "ig": {"class_type": "BasicGuider", "inputs": {"model": ["ish", 0], "conditioning": [N_IMG_ENC, 0]}},
         "in": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
         "is": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
         "im": {"class_type": "ManualSigmas", "inputs": {"sigmas": p["sigmas"]}},
         N_IMG_SAMPLER: {"class_type": "SamplerCustomAdvanced", "inputs": {
             "noise": ["in", 0], "guider": ["ig", 0], "sampler": ["is", 0], "sigmas": ["im", 0],
             "latent_image": [N_IMG_ENC, 1]}},
         "id": {"class_type": "VAEDecode", "inputs": {"samples": [N_IMG_SAMPLER, 0], "vae": ["iv", 0]}},
         N_IMG_SAVE: {"class_type": "SaveImage", "inputs": {"images": ["id", 0], "filename_prefix": "H3Toybox/img"}}}
    for i, item in enumerate(refs, 1):  # image_1 is the picture edited, the rest are references, in order
        g[f"ir{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["comfy_name"]}}
        g[N_IMG_ENC]["inputs"][f"images.image_{i}"] = [f"ir{i}", 0]
    return g


def image_graph(cfg, prompt, w, h, refs, seed):
    if ph3_mode(cfg):
        return ph3_graph(cfg, prompt, w, h, refs, seed)
    im = cfg["image"]
    g = {"iu": {"class_type": "UNETLoader", "inputs": {"unet_name": im["unet"], "weight_dtype": "default"}},
         "ic": {"class_type": "QwenImage21Cache", "inputs": {"model": ["iu", 0], "device": "auto", "dtype": "default"}},
         "il": {"class_type": "CLIPLoader", "inputs": {"clip_name": im["clip"], "type": "qwen_image", "device": "default"}},
         "iv": {"class_type": "VAELoader", "inputs": {"vae_name": im["vae"]}},
         N_IMG_ENC: {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["il", 0], "prompt": prompt,
                                                                      "negative_prompt": "", "resolution": 1024,
                                                                      "vae": ["iv", 0]}},
         "ie": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
         N_IMG_SAMPLER: {"class_type": "KSampler", "inputs": {
             "model": ["ic", 0], "positive": [N_IMG_ENC, 0], "negative": [N_IMG_ENC, 1], "latent_image": ["ie", 0],
             "seed": seed, "steps": int(im["steps"]), "cfg": float(im["cfg"]), "sampler_name": "euler",
             "scheduler": "simple", "denoise": 1.0}},
         "id": {"class_type": "VAEDecode", "inputs": {"samples": [N_IMG_SAMPLER, 0], "vae": ["iv", 0]}},
         N_IMG_SAVE: {"class_type": "SaveImage", "inputs": {"images": ["id", 0], "filename_prefix": "H3Toybox/img"}}}
    for i, item in enumerate(refs, 1):  # the encoder's dynamic input: slot N must carry reference N
        g[f"ir{i}"] = {"class_type": "LoadImage", "inputs": {"image": item["comfy_name"]}}
        g[N_IMG_ENC]["inputs"][f"images.image_{i}"] = [f"ir{i}", 0]
    return g


def check_image_wiring(entry, refs):
    """Read back the graph ComfyUI really ran. A LoadImage that exists but is not wired into the encoder's
    dynamic `images` input passes every other check and silently turns an edit into plain text-to-image."""
    ran = (entry.get("prompt") or [None, None, {}])[2]
    enc = ran.get(N_IMG_ENC, {})
    for i, item in enumerate(refs, 1):
        link = enc.get("inputs", {}).get(f"images.image_{i}")
        src = ran.get(link[0], {}) if isinstance(link, list) else {}
        if src.get("class_type") != "LoadImage" or src.get("inputs", {}).get("image") != item["comfy_name"]:
            return f"參考圖第 {i} 張沒有接進 {enc.get('class_type', '編碼節點')} 的 images.image_{i}"
    return None


def rewrite_image_prompt(cfg, tagged, refs):
    """Optional: rewrite with Qwen-Image's official rewriter spec (remote providers only; the local 9B is
    trained for H3 video prompts, not pictures). The spec answers in JSON; only the prompt goes to Qwen."""
    rw = cfg["rewriter"]
    provider = rw.get("provider", "local")
    if provider not in ("gemini", "openai"):
        raise ValueError("圖片的提示詞改寫要用 Gemini 或 OpenAI 端點（本地 9B 是影片專用的改寫模型）")
    spec = Path(cfg["image"]["ph3"]["spec"] if ph3_mode(cfg) else cfg["image"]["spec_edit" if refs else "spec_t2i"]
                ).read_text(encoding="utf-8")
    system = spec + "\n\nReturn only the final rewritten prompt, nothing else."
    parts = [("text", f"User input: {tagged}\nNumber of input images: {len(refs)}")]
    for i, item in enumerate(refs, 1):
        with Image.open(media_path(item)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((int(rw["image_side"]),) * 2)  # keep its real shape: the spec reads the ratio off it
            parts += [("text", f"<Picture {i}>:" if ph3_mode(cfg) else f"<image{i}>:"), ("image", im)]
    out = (rewrite_gemini(rw["gemini"], rw, system, parts) if provider == "gemini"
           else rewrite_openai(rw["openai"], rw, system, parts))
    out = re.sub(r"^```\w*\n|\n```$", "", out.strip())
    m = re.search(r"\{.*\}", out, flags=re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            out = str(data.get("rewritten_prompt") or data.get("prompt") or out).strip()
        except ValueError:
            pass
    return out


def generate_image(cfg, req):
    if not req.get("prompt", "").strip():
        raise ValueError("請先寫一段描述喔")
    if not cfg["image"].get("installed", True):  # Colab: the picture models are an optional download
        raise ValueError("這台還沒有安裝生圖模型：回到 Colab 第 ② 格勾選「INSTALL_IMAGE」再執行一次，然後重跑 ③")
    tagged, refs = image_refs(req, req["prompt"])
    final_text = req.get("final_prompt") or tagged
    w, h = image_dims(req.get("aspect", "auto"), req.get("image_size", "1.3K"), refs)
    seed = req.get("seed")
    seed = int(seed) if seed not in (None, "", -1) else random.randint(0, 2**48)
    prompt_id = queue_prompt(cfg, image_graph(cfg, final_text, w, h, refs, seed))
    job = {"id": prompt_id, "created": time.time(), "status": "queued", "mode": "image",
           "task": "Edit" if refs else "T2I", "prompt": req["prompt"], "final_prompt": final_text, "seed": seed,
           "aspect": req.get("aspect", "auto"), "image_size": req.get("image_size", "1.3K"), "width": w, "height": h,
           "tray": req.get("tray", []), "refs": [r["comfy_name"] for r in refs],
           "engine": cfg["image"].get("engine", "qwen"),
           "steps": 8 if ph3_mode(cfg) else int(cfg["image"]["steps"])}
    with lock:
        jobs = load_json(JOBS_PATH, [])
        jobs.insert(0, job)
        save_json(JOBS_PATH, jobs)
    return job


def saved_image(entry):
    for f in entry.get("outputs", {}).get(N_IMG_SAVE, {}).get("images", []) or []:
        if f.get("type", "output") == "output":
            return f
    return None


def image_estimate(job, st, stats):
    """(stage, percent, eta): model loading first, then KSampler steps at the measured median pace."""
    n = int(job.get("steps", 25))
    step = stats.get("img_step", 1.2)
    pre = stats.get("img_pre", 20.0)
    if not st or not st.get("started"):
        return "queued", 0, pre + n * step
    elapsed = time.time() - st["started"]
    steps = st["steps"]
    if not steps:
        return "prepare", max(1, min(30, int(100 * elapsed / (elapsed + pre + n * step)))), max(3.0, pre - elapsed) + n * step
    durs = [steps[i][0] - steps[i - 1][0] for i in range(1, len(steps))]
    if durs:
        step = sorted(durs)[len(durs) // 2]
    v = steps[-1][1]
    if v >= n:
        return "decode", 97, 3
    remain = (n - v) * step + 3
    return "draw", max(1, min(99, int(100 * elapsed / (elapsed + remain)))), remain


def learn_image(st):
    steps = (st or {}).get("steps") or []
    if not st or not st.get("started") or len(steps) < 3:
        return
    durs = [steps[i][0] - steps[i - 1][0] for i in range(1, len(steps))]
    sample = {"img_pre": max(0.0, steps[0][0] - st["started"]), "img_step": sorted(durs)[len(durs) // 2]}
    with lock:
        stats = load_json(STATS_PATH, DEFAULT_STATS)
        for k, v in sample.items():
            stats[k] = round(stats[k] * 0.6 + v * 0.4, 3) if k in stats else round(v, 3)
        save_json(STATS_PATH, stats)


def saved_video(entry):
    """The video our SaveVideo node wrote.

    LoadVideo also reports its input file as a UI output (type "input"), so only files of
    type "output" count, and the save node ("sv"; "364" for H3 Studio's VHS jobs) is preferred.
    """
    outputs = entry.get("outputs", {})
    ordered = [outputs[k] for k in ("sv", "364") if k in outputs] + list(outputs.values())
    for node_out in ordered:
        for key in ("images", "gifs", "videos", "video"):
            for f in node_out.get(key, []) or []:
                if (isinstance(f, dict) and f.get("type", "output") == "output"
                        and str(f.get("filename", "")).lower().endswith((".mp4", ".webm", ".mkv", ".mov"))):
                    return f
    return None


def finish_extend(cfg, job):
    """Join the conformed source and the new clip into one longer video."""
    src = asset(job["extend"]["source_id"])
    OUTPUTS.mkdir(exist_ok=True)
    part = OUTPUTS / f"{job['id']}_part.mp4"
    comfy_download(cfg, job["video"], part)
    full_src = OUTPUTS / f"{job['id']}_src.mp4"
    conform(LIBRARY / src["file"], full_src, job["width"], job["height"], src.get("has_audio"))
    name = f"{job['id']}_full.mp4"
    join_clips(full_src, part, OUTPUTS / name)
    for p in (part, full_src):
        p.unlink(missing_ok=True)
    (Path(cfg["comfy_input_dir"]) / job["extend"]["tail"]).unlink(missing_ok=True)
    return name


def refresh_jobs(cfg):
    # the browser poll and the 🤖 Agent worker both refresh; serialized so a finished extension
    # is never handed to two join workers
    with REFRESH_LOCK:
        return _refresh_jobs(cfg)


def _refresh_jobs(cfg):
    with lock:
        jobs = load_json(JOBS_PATH, [])
    pending = [j for j in jobs if j["status"] in ("queued", "running", "joining")]
    if pending:
        try:
            q = comfy(cfg, "/queue")
        except OSError:
            return jobs
        running = {item[1] for item in q.get("queue_running", [])}
        for job in pending:
            if job["status"] == "joining":
                continue
            entry = history_entry(cfg, job["id"])
            if entry and job.get("mode") == "image":
                learn_image(MONITOR.snapshot(job["id"]))
                if entry["status"].get("status_str") == "error":
                    job.update(status="error", error=execution_error(entry))
                    continue
                miswired = check_image_wiring(entry, [{"comfy_name": n} for n in job.get("refs", [])])
                job.update(image=saved_image(entry), finished=time.time(), status="done")
                if miswired:
                    job.update(status="error", error=miswired)
                continue
            if entry:
                st = MONITOR.snapshot(job["id"])
                if st:
                    learn(job, st)
                if entry["status"].get("status_str") == "error":
                    job.update(status="error", error=execution_error(entry))
                    continue
                job.update(video=saved_video(entry), finished=time.time())
                if job.get("extend") and job["video"]:
                    job["status"] = "joining"
                    threading.Thread(target=join_worker, args=(cfg, dict(job)), daemon=True).start()
                else:
                    job["status"] = "done"
            elif job["id"] in running:
                job["status"] = "running"
        with lock:
            stored = {j["id"]: j for j in load_json(JOBS_PATH, [])}
            stored.update({j["id"]: j for j in pending})
            jobs = sorted(stored.values(), key=lambda j: -j["created"])
            save_json(JOBS_PATH, jobs)
    stats = load_json(STATS_PATH, DEFAULT_STATS)
    for job in jobs:
        if job["status"] in ("queued", "running"):
            est = image_estimate if job.get("mode") == "image" else estimate
            stage, percent, eta = est(job, MONITOR.snapshot(job["id"]), stats)
            job["progress"] = {"stage": stage, "percent": percent, "eta": round(eta) if eta is not None else None,
                               "live": MONITOR.connected}
        elif job["status"] == "joining":
            job["progress"] = {"stage": "join", "percent": 99, "eta": 5, "live": True}
    return jobs


def join_worker(cfg, job):
    try:
        update = {"status": "done", "full_video": finish_extend(cfg, job)}
    except Exception as e:
        update = {"status": "done", "join_error": f"接回原片失敗：{e}"}
    with lock:
        jobs = load_json(JOBS_PATH, [])
        for j in jobs:
            if j["id"] == job["id"]:
                j.update(update)
        save_json(JOBS_PATH, jobs)


# ---------------------------------------------------------------- 🤖 短片 Agent
# The T8 pattern for small models: ONE compact structured call writes the whole short as a contract,
# everything mechanical (durations, reference labels, line budget, chaining) is checked and derived locally,
# and only a failed check goes back to the model, once, with the exact problems listed.
# Each segment then runs through the ordinary 🧸 參考 path (9B rewriter → Ref2VA) and is chained onto the
# previous one with H3 Motion Context, so the last segment's full video is the whole short.

AGENT_PATH = DATA / "agent_runs.json"
AGENT_CPS = 4.0  # spoken characters per second a segment can carry
AGENT_SYSTEM = """你是短片導演。使用者給你幾張參考圖（標籤：{labels}）和一句指示，你要規劃一支 {total} 秒的短片，
分成 {n} 段；每段是一次影片生成（4 到 15 秒），段與段會直接接在一起播放。

只輸出一個 JSON 物件，不要任何說明：
{{"title": "片名",
  "logline": "一句話說完整個故事",
  "bible": {{"characters": [{{"ref": "圖片1", "name": "名字", "look": "外觀與服裝，一句"}}],
            "location": "主要場景、時間與光線，一句",
            "style": "影像風格，一句"}},
  "segments": [{{"seconds": 10,
                "refs": ["圖片1"],
                "action": "這一段畫面裡實際發生的事：誰在畫面哪一側、做了什麼具體動作、鏡頭怎麼帶",
                "dialogue": [{{"who": "名字", "line": "台詞"}}],
                "sound": "環境聲與音效",
                "end_state": "這一段最後一刻的畫面：每個人在哪、什麼姿勢、手上拿著什麼"}}]}}

規則：
- 一段只做一件事；第 k 段從第 k-1 段的 end_state 開始，因果要接得上，最後一段要有收尾。
- 每段的台詞總字數不超過「秒數×4」；可以沒有台詞（dialogue 給空陣列）。
- refs 只能用上面列出的標籤；角色出現在哪一段，那一段的 refs 就要有他的圖。
- 所有段的 seconds 加起來必須等於 {total}。
- 寫看得見、拍得出來的東西，不寫心理活動。"""
AGENT_REVISE = """下面是目前這支短片的規劃 JSON。使用者想修改：{message}

只改使用者要求的部分，其他段落一字不動；輸出修改後的完整 JSON（同樣格式、同樣的總秒數 {total}）。

目前的規劃：
{plan}"""
AGENT_LOCK = threading.Lock()
REFRESH_LOCK = threading.Lock()
AGENT_CANCEL = set()


def agent_runs():
    return load_json(AGENT_PATH, [])


def agent_update(run_id, **fields):
    with AGENT_LOCK:
        runs = agent_runs()
        for r in runs:
            if r["id"] == run_id:
                r.update(fields)
                run = dict(r)
        save_json(AGENT_PATH, runs)
    return run


def agent_log(run_id, text):
    with AGENT_LOCK:
        runs = agent_runs()
        for r in runs:
            if r["id"] == run_id:
                r.setdefault("log", []).append([round(time.time()), text])
        save_json(AGENT_PATH, runs)


def agent_chat(cfg, system, parts):
    """The planner: OpenAI-compatible (local MoE) or Gemini (key read from a file at call time)."""
    ag = cfg["agent"]
    knobs = {"temperature": float(ag["temperature"]), "top_p": 0.9, "max_length": 4096}
    if ag.get("provider") == "gemini":
        key_file = Path(ag.get("gemini_key_file") or "")
        key = key_file.read_text(encoding="utf-8").strip() if key_file.is_file() else cfg["rewriter"]["gemini"].get("api_key", "")
        return rewrite_gemini({"api_key": key, "model": ag.get("gemini_model")}, knobs, system, parts, max_tokens=8192)
    return rewrite_openai({"base_url": ag["base_url"], "api_key": "", "model": ag.get("model")}, knobs, system, parts,
                          max_tokens=4096)


def parse_contract(text):
    text = re.sub(r"^```\w*\n|\n```$", "", (text or "").strip())
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        try:  # the most common small-model slip: a trailing comma before } or ]
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except ValueError:
            return None


def spoken(seg):
    return "".join(str(d.get("line", "")) for d in seg.get("dialogue") or [] if isinstance(d, dict))


def check_contract(plan, labels, total):
    """Local contract checks. Mechanical slips (durations) are fixed here; content problems are returned
    as a list for one correction round, the way T8's quality layer sends a prompt back."""
    errors, fixes = [], []
    segs = plan.get("segments") if isinstance(plan, dict) else None
    if not isinstance(segs, list) or not segs:
        return plan, ["沒有 segments 陣列"], fixes
    for k, seg in enumerate(segs, 1):
        if not isinstance(seg, dict):
            errors.append(f"第 {k} 段不是物件")
            continue
        try:
            sec = float(seg.get("seconds", 0))
        except (TypeError, ValueError):
            sec = 0
        if not 4 <= sec <= 15:
            fixes.append(f"第 {k} 段 {sec:g} 秒 → 夾到 4–15")
            sec = min(15.0, max(4.0, sec or 8.0))
        seg["seconds"] = sec
        bad = [r for r in seg.get("refs") or [] if r not in labels]
        if bad:
            errors.append(f"第 {k} 段的 refs 用了不存在的標籤 {bad}（只能用 {labels}）")
        seg["refs"] = [r for r in seg.get("refs") or [] if r in labels]
        if len(str(seg.get("action", ""))) < 15:
            errors.append(f"第 {k} 段的 action 太短，要寫出看得見的具體動作與鏡頭")
        if not str(seg.get("end_state", "")).strip():
            errors.append(f"第 {k} 段缺 end_state（下一段要從這裡接）")
        n_chars = len(re.sub(r"[\s，。！？、,.!?…「」\"']", "", spoken(seg)))
        if n_chars > sec * AGENT_CPS * 1.25:
            errors.append(f"第 {k} 段台詞 {n_chars} 字，{sec:g} 秒最多約 {int(sec * AGENT_CPS)} 字：刪短或拆段")
    secs = [s["seconds"] for s in segs if isinstance(s, dict)]
    if secs and abs(sum(secs) - total) > 0.5:  # rescale, then let the last segment absorb rounding
        scale = total / sum(secs)
        for s in segs[:-1]:
            s["seconds"] = float(min(15, max(4, round(s["seconds"] * scale))))
        segs[-1]["seconds"] = float(min(15, max(4, total - sum(s["seconds"] for s in segs[:-1]))))
        fixes.append(f"總秒數 {sum(secs):g} → 依比例調成 {sum(s['seconds'] for s in segs):g}")
    return plan, errors, fixes


def agent_plan(cfg, run, revise=None):
    """One planning call (+ at most one correction call). Returns the checked plan."""
    labels = [f"圖片{i}" for i in range(1, len(run["images"]) + 1)]
    total = int(run["total"])
    n = max(1, -(-total // 15))  # ceil(total / 15): fewest segments that fit, each ≤ 15 s
    system = AGENT_SYSTEM.format(labels="、".join(labels) or "（沒有參考圖）", total=total, n=n)
    parts = []
    for label, aid in zip(labels, run["images"]):
        with Image.open(media_path(asset(aid))) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((512, 512))
            parts += [("text", f"{label}："), ("image", im)]
    if revise:
        parts.append(("text", AGENT_REVISE.format(message=revise, total=total,
                                                  plan=json.dumps(run["plan"], ensure_ascii=False, indent=1))))
    else:
        parts.append(("text", f"指示：{run['instruction']}"))
    t0 = time.time()
    raw = agent_chat(cfg, system, parts)
    plan = parse_contract(raw)
    errors = ["輸出不是合法的 JSON"] if plan is None else check_contract(plan, labels, total)[1]
    rounds = 1
    if errors:  # one correction round with the exact problems, like T8's correction_messages
        agent_log(run["id"], "契約檢查沒過，送回修正：" + "；".join(errors))
        fix = parts + [("text", "你上一版的輸出：\n" + raw[:6000] + "\n\n有這些問題，只修正它們，輸出完整 JSON：\n- "
                        + "\n- ".join(errors))]
        raw2 = agent_chat(cfg, system, fix)
        plan2 = parse_contract(raw2)
        rounds = 2
        if plan2 is not None:
            plan, raw = plan2, raw2
    if plan is None:
        raise RuntimeError("規劃模型沒有給出合法的 JSON：" + raw[:300])
    plan, errors, fixes = check_contract(plan, labels, total)
    return plan, {"seconds": round(time.time() - t0, 1), "rounds": rounds, "errors": errors, "fixes": fixes}


def segment_prompt(plan, k):
    """A 🧸 參考 prompt for segment k: bible facts the segment needs, then what happens, all in plain Chinese
    with @圖片N tags. The 9B rewriter turns it into the official Ref2VA prompt."""
    seg, bible = plan["segments"][k], plan.get("bible") or {}
    refs = seg.get("refs") or []
    lines = [x for x in (bible.get("style"), bible.get("location")) if x]
    used = set()
    for c in bible.get("characters") or []:
        if isinstance(c, dict) and c.get("ref") in refs:
            lines.append(f"{c.get('name', '')}（@{c['ref']}）：{c.get('look', '')}")
            used.add(c["ref"])
    for r in refs:
        if r not in used:
            lines.append(f"參考 @{r}")
    if k > 0:
        lines.append("緊接上一段的最後一刻：" + str(plan["segments"][k - 1].get("end_state", "")))
    lines.append(str(seg.get("action", "")))
    for d in seg.get("dialogue") or []:
        if isinstance(d, dict) and d.get("line"):
            lines.append(f"{d.get('who', '')}說：「{d['line']}」")
    if seg.get("sound"):
        lines.append("聲音：" + str(seg["sound"]))
    lines.append("這一段結束在：" + str(seg.get("end_state", "")))
    return "\n".join(lines)


def wait_job(cfg, job_id, run_id):
    while True:
        if run_id in AGENT_CANCEL:
            raise RuntimeError("已停止")
        job = next((j for j in refresh_jobs(cfg) if j["id"] == job_id), None)
        if job is None:
            raise RuntimeError("工作不見了（被移除了？）")
        if job["status"] == "error":
            raise RuntimeError(job.get("error") or "生成失敗")
        if job["status"] == "done":
            return job
        time.sleep(4)


def agent_worker(cfg, run_id, start=0, revise=None):
    try:
        run = next(r for r in agent_runs() if r["id"] == run_id)
        cfg = copy.deepcopy(cfg)
        cfg["agent"]["provider"] = run.get("planner") or cfg["agent"]["provider"]
        if revise is not None or not run.get("plan"):
            agent_update(run_id, status="planning")
            plan, meta = agent_plan(cfg, run, revise)
            if revise is not None:  # first segment whose contract changed; everything after it re-chains
                old = run.get("plan") or {}
                same_bible = old.get("bible") == plan.get("bible")
                start = 0
                if same_bible:
                    olds = old.get("segments") or []
                    while start < min(len(olds), len(plan["segments"])) and olds[start] == plan["segments"][start]:
                        start += 1
                agent_log(run_id, f"修改：{revise} → 從第 {start + 1} 段開始重做")
            segs = (run.get("segments") or [])[:start] + [{"status": "waiting"} for _ in plan["segments"][start:]]
            run = agent_update(run_id, plan=plan, plan_meta=meta, segments=segs, status="generating", final=None)
            agent_log(run_id, f"規劃完成（{meta['seconds']} 秒，{meta['rounds']} 輪）" +
                      (f"；仍有提醒：{'；'.join(meta['errors'])}" if meta["errors"] else ""))
        plan = run["plan"]
        prev_asset = run["segments"][start - 1].get("asset") if start > 0 else None
        for k in range(start, len(plan["segments"])):
            seg = plan["segments"][k]
            tray = [{"id": aid, "use_audio": True, "extend": False} for aid in run["images"]]
            if prev_asset and run.get("chain", True):
                tray.append({"id": prev_asset, "use_audio": True, "extend": True})
            req = {"mode": "ref", "prompt": segment_prompt(plan, k), "tray": tray, "seconds": seg["seconds"],
                   "aspect": run["aspect"], "resolution": run["resolution"], "intensity": run.get("intensity", "off"),
                   "skill": run.get("skill", ""), "fast": True, "seed": None}
            segs = agent_runs_get(run_id)["segments"]
            segs[k] = {"status": "rewriting", "prompt": req["prompt"]}
            agent_update(run_id, segments=segs)
            req["final_prompt"] = rewrite(cfg, req)["prompt"]
            job = generate(cfg, req)
            segs[k].update(status="generating", job=job["id"])
            agent_update(run_id, segments=segs)
            job = wait_job(cfg, job["id"], run_id)
            item = add_output_to_library(cfg, job["id"], run.get("folder"))
            prev_asset = item["id"]
            segs[k].update(status="done", asset=item["id"], video=job.get("full_video") or None)
            agent_update(run_id, segments=segs)
        last = agent_runs_get(run_id)["segments"][-1]
        final_job = next((j for j in load_json(JOBS_PATH, []) if j["id"] == last.get("job")), {})
        agent_update(run_id, status="done", final={"full_video": final_job.get("full_video"), "video": final_job.get("video")})
        agent_log(run_id, "完成 🎬")
    except Exception as e:
        agent_update(run_id, status="error", error=f"{type(e).__name__}: {e}")
    finally:
        AGENT_CANCEL.discard(run_id)


def agent_runs_get(run_id):
    return next(r for r in agent_runs() if r["id"] == run_id)


def agent_start(cfg, b):
    images = [i for i in b.get("images", []) if asset(i)["kind"] == "image"]
    if not b.get("instruction", "").strip():
        raise ValueError("請寫一句你想要的短片")
    total = int(b.get("total", 30))
    if not 8 <= total <= int(cfg["agent"]["max_seconds"]):
        raise ValueError(f"短片長度要在 8 到 {cfg['agent']['max_seconds']} 秒之間")
    run_id = uuid.uuid4().hex[:10]
    folder = folder_action({"action": "create", "name": "🤖 " + b["instruction"][:12]})[-1]["id"]
    run = {"id": run_id, "created": time.time(), "status": "queued", "instruction": b["instruction"], "images": images,
           "total": total, "aspect": b.get("aspect", "9:16") if b.get("aspect") != "auto" else "9:16",
           "resolution": b.get("resolution", "720P"), "intensity": b.get("intensity", "off"), "skill": b.get("skill", ""),
           "chain": bool(b.get("chain", True)), "folder": folder,
           "planner": b.get("planner") if b.get("planner") in ("openai", "gemini") else cfg["agent"]["provider"], "log": []}
    with AGENT_LOCK:
        runs = agent_runs()
        runs.insert(0, run)
        save_json(AGENT_PATH, runs)
    threading.Thread(target=agent_worker, args=(cfg, run_id), daemon=True).start()
    return run


def agent_revise(cfg, b):
    run = agent_runs_get(b["id"])
    if run["status"] in ("planning", "generating"):
        raise ValueError("這支短片還在做，等它好了再改（或先停止）")
    if not run.get("plan"):
        raise ValueError("還沒有規劃可以改")
    threading.Thread(target=agent_worker, args=(cfg, run["id"]), kwargs={"revise": b["message"]}, daemon=True).start()
    return agent_update(run["id"], status="planning")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not path.is_file():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def body_json(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        cfg = load_config()
        try:
            if url.path == "/":
                return self.send_file(STATIC / "index.html")
            if url.path.startswith("/static/"):
                return self.send_file(safe_child(STATIC, url.path[len("/static/"):]))
            if url.path.startswith("/library/"):
                return self.send_file(safe_child(LIBRARY, url.path[len("/library/"):]))
            if url.path.startswith("/outputs/"):
                return self.send_file(safe_child(OUTPUTS, url.path[len("/outputs/"):]))
            if url.path == "/api/folders":
                return self.send_json(load_json(FOLDERS_PATH, []))
            if url.path == "/api/library":
                return self.send_json(library_index())
            if url.path == "/api/jobs":
                return self.send_json(refresh_jobs(cfg))
            if url.path == "/api/agent/runs":
                return self.send_json(agent_runs())
            if url.path == "/api/config":
                wf = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
                loras = [{"id": k, "name": v["inputs"]["lora_name"], "strength": v["inputs"]["strength_model"]}
                         for k, v in wf.items() if v["class_type"] == "LoraLoaderModelOnly"]
                stats = load_json(STATS_PATH, DEFAULT_STATS)
                return self.send_json({"config": public_config(cfg), "skills": load_skills(cfg), "aspects": list(ASPECTS), "image_sizes": list(IMAGE_SIZES),
                                       "lora_nodes": loras, "rewrite_seconds": stats["rewrite"]})
            if url.path == "/api/models":
                info = comfy(cfg, "/object_info/CLIPLoader")
                names = info["CLIPLoader"]["input"]["required"]["clip_name"][0]
                return self.send_json([n for n in names if "qwen3.5" in n.lower() or "rewriter" in n.lower()])
            if url.path == "/api/view":
                q = urllib.parse.urlencode(dict(urllib.parse.parse_qsl(url.query)))
                with urllib.request.urlopen(cfg["comfy_url"].rstrip("/") + "/view?" + q) as r:
                    data = r.read()
                    ctype = r.headers.get("Content-Type", "video/mp4")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            self.send_error(404)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        cfg = load_config()
        try:
            if url.path == "/api/upload":
                name = urllib.parse.unquote(self.headers.get("X-Filename", "upload"))
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                folder = urllib.parse.unquote(self.headers.get("X-Folder", "")) or None
                return self.send_json(add_asset(cfg, name, body, folder))
            if url.path == "/api/library/delete":
                delete_asset(cfg, self.body_json()["id"])
                return self.send_json({"ok": True})
            if url.path == "/api/library/from_job":
                b = self.body_json()
                return self.send_json(add_output_to_library(cfg, b["id"], b.get("folder")))
            if url.path == "/api/folders":
                return self.send_json(folder_action(self.body_json()))
            if url.path == "/api/library/move":
                b = self.body_json()
                return self.send_json(move_assets(b["ids"], b.get("folder")))
            if url.path == "/api/rewriter/test":
                return self.send_json(test_rewriter(cfg, self.body_json()))
            if url.path == "/api/rewrite":
                b = self.body_json()
                if b.get("mode") == "image":
                    t0 = time.time()
                    tagged, refs = image_refs(b, b.get("prompt", ""))
                    return self.send_json({"prompt": rewrite_image_prompt(cfg, tagged, refs), "task": "Edit" if refs else "T2I",
                                           "seconds_used": round(time.time() - t0, 1)})
                return self.send_json(rewrite(cfg, b))
            if url.path == "/api/generate":
                b = self.body_json()
                return self.send_json(generate_image(cfg, b) if b.get("mode") == "image" else generate(cfg, b))
            if url.path == "/api/agent/start":
                return self.send_json(agent_start(cfg, self.body_json()))
            if url.path == "/api/agent/revise":
                return self.send_json(agent_revise(cfg, self.body_json()))
            if url.path == "/api/agent/stop":
                AGENT_CANCEL.add(self.body_json()["id"])
                return self.send_json({"ok": True})
            if url.path == "/api/jobs/delete":
                job_id = self.body_json()["id"]
                with lock:
                    save_json(JOBS_PATH, [j for j in load_json(JOBS_PATH, []) if j["id"] != job_id])
                return self.send_json({"ok": True})
            if url.path == "/api/config":
                new = self.body_json()
                save_config(new)
                return self.send_json({"ok": True})
            self.send_error(404)
        except subprocess.CalledProcessError as e:
            self.send_json({"error": "ffmpeg 失敗：" + (e.stderr or b"").decode("utf-8", "replace")[-800:]}, 400)
        except (ValueError, RuntimeError, KeyError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)


def safe_child(base, rel):
    path = (base / urllib.parse.unquote(rel)).resolve()
    if base.resolve() not in path.parents:
        raise ValueError("invalid path")
    return path


if __name__ == "__main__":
    LIBRARY.mkdir(parents=True, exist_ok=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    n = restore_inputs(load_config())
    if n:
        print(f"Restored {n} library files into ComfyUI's input folder")
    # Windows lets a second process bind the same port when SO_REUSEADDR is set; an old server
    # would then keep answering. Refuse instead, so a double start fails loudly.
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    MONITOR.start()
    print(f"H3 Toybox running at http://127.0.0.1:{PORT}  (data: {DATA})")
    httpd.serve_forever()
