"""MiniMax / 豆包引擎纯代码验证 —— Mock 假网关覆盖协议分支（不花真钱）。

覆盖 PLAN §8.3 T1+T2：
- 请求体 wire 断言（voice_id 必填/默认回落/格式字段）
- 同步合成 hex 解码；TAR 归档解包取音频成员
- base_resp 错误归一化；HTML 错误页；空 audio
- voice_design/voice_clone 成功与 input_sensitive 拦截
- 豆包 references base64 / text_prompt 前缀 / code!=0 / 字数超限
- 引擎级 synthesize（Mock HTTP）逐轮合成 + 轮缓存命中
- 音色×引擎亲和校验（voice_store.supported_engines）
运行: python tests/test_api_engines.py
"""
from __future__ import annotations
import base64, io, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tts_service import minimax_engine as mx          # noqa: E402
from tts_service import doubao_engine as db           # noqa: E402
from webapp import voice_store                        # noqa: E402

PASS = FAIL = 0
def ok(cond, name):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}")

FAKE_MP3 = b"\xff\xfb\x90" + b"\x00" * 200            # 假 mp3 头 + 填充
HEX_AUDIO = FAKE_MP3.hex()

def make_tar(members: dict[str, bytes]) -> bytes:
    """最小 ustar 归档构造（size 字段必须补满 12 字节，否则头部错位）。"""
    out = bytearray()
    for name, data in members.items():
        h = bytearray(512)
        nb = name.encode()
        h[:len(nb)] = nb
        h[124:136] = f"{len(data):o}".encode().rjust(11, b"0") + b"\0"
        h[257:262] = b"ustar"
        out += h + data + b"\0" * ((512 - len(data) % 512) % 512)
    out += b"\0" * 1024
    return bytes(out)


def real_wav(seconds: float = 0.3, rate: int = 16000) -> bytes:
    """生成合法 WAV（静音），供 ffmpeg 拼接链路使用。"""
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class MockMiniMax(BaseHTTPRequestHandler):
    behavior = {"mode": "ok"}
    def log_message(self, *a): pass
    def _send(self, obj, status=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        mode = MockMiniMax.behavior["mode"]
        if self.path == "/v1/t2a_v2":
            if mode == "http_error":
                return self._send(b"<html>502 Bad Gateway</html>", status=502, ctype="text/html")
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
            MockMiniMax.behavior["last_t2a"] = req
            if mode == "bad_base_resp":
                return self._send({"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}})
            if mode == "empty_audio":
                return self._send({"data": {"audio": ""}, "base_resp": {"status_code": 0}})
            return self._send({"data": {"audio": HEX_AUDIO}, "base_resp": {"status_code": 0}})
        if self.path == "/v1/voice_design":
            if mode == "sensitive":
                return self._send({"voice_id": "vd_x", "trial_audio": HEX_AUDIO,
                                   "base_resp": {"status_code": 0}})
            return self._send({"voice_id": "vd_design01", "trial_audio": HEX_AUDIO,
                               "base_resp": {"status_code": 0}})
        if self.path == "/v1/t2a_v2" and MockMiniMax.behavior.get("design_flow"):
            # 音色转正链路里的长参考合成
            return self._send({"data": {"audio": HEX_AUDIO},
                               "base_resp": {"status_code": 0}})
        if self.path == "/v1/files/upload":
            return self._send({"file": {"file_id": 42}, "base_resp": {"status_code": 0}})
        if self.path == "/v1/voice_clone":
            if mode == "sensitive":
                return self._send({"input_sensitive": True, "base_resp": {"status_code": 0}})
            return self._send({"input_sensitive": False, "base_resp": {"status_code": 0}})
        return self._send({"base_resp": {"status_code": 999, "status_msg": "unknown path"}})


class MockDoubao(BaseHTTPRequestHandler):
    behavior = {"mode": "ok", "calls": 0}
    def log_message(self, *a): pass
    def do_POST(self):
        MockDoubao.behavior["calls"] += 1
        mode = MockDoubao.behavior["mode"]
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length) or b"{}")
        MockDoubao.behavior["last_req"] = req
        if self.headers.get("X-Api-Key") != "k-test":
            return self._resp({"code": 1000, "message": "invalid key"})
        if mode == "bad_code":
            return self._resp({"code": 3001, "message": "quota exceeded"})
        if mode == "empty_audio":
            return self._resp({"code": 0, "audio": ""})
        wav = MockDoubao.behavior.get("wav_bytes") or real_wav()
        return self._resp({"code": 0, "audio": base64.b64encode(wav).decode(),
                           "subtitle": {"text": "x"}})
    def _resp(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def main():
    os.environ["MINIMAX_API_KEY"] = "k-mock"
    os.environ["DOUBAO_API_KEY"] = "k-mock"

    print("== 1. 请求体 wire 断言 ==")
    b = mx.build_t2a_body("  你好 token  ", "fem-01")
    ok(b["text"] == "你好 token", "t2a 文本 trim")
    ok(b["voice_setting"]["voice_id"] == "fem-01", "t2a voice_id 必填传入")
    b2 = mx.build_t2a_body("x", "")
    ok(b2["voice_setting"]["voice_id"] == mx.DEFAULT_VOICE, "t2a voice_id 空回落默认")
    ok(b2["audio_setting"]["format"] == "mp3", "t2a 默认 mp3")
    try:
        mx.build_t2a_body("   ", "v"); ok(False, "空文本应拒绝")
    except mx.MiniMaxError: ok(True, "空文本应拒绝")
    try:
        mx.build_voice_clone_body(1, "bad id!")
    except mx.MiniMaxError: ok(True, "voice_id 命名校验拦截")
    else: ok(False, "voice_id 命名校验拦截")
    ok(mx.VALID_VOICE_ID.match("canvas-voice-ab12cd34"), "voice_id 合法样例通过")

    dbb = db.build_speech_body("你好", ["AAAA"], "speaker-x")
    ok(dbb["model"] == "seed-audio-1.0", "豆包模型固定 seed-audio-1.0")
    ok(dbb["references"][0]["audio_data"] == "AAAA", "豆包 references base64 透传")
    ok(dbb["speaker"] == "speaker-x", "豆包 speaker 透传")
    ok(db.reference_prompt_prefix(1) in dbb["text_prompt"], "豆包 text_prompt 参考音前缀")
    ok(dbb["audio_config"]["enable_subtitle"] is True, "豆包字幕开启")
    ok(dbb["watermark"]["aigc_watermark"] is False, "豆包水印字段（永久关闭）")
    try:
        db.build_speech_body("x" * 10001, ["A" * 100])
        ok(False, "豆包字数超限应拒绝")
    except db.DoubaoError: ok(True, "豆包字数超限应拒绝")
    noref = db.build_speech_body("你好", None)
    ok(db.reference_prompt_prefix(1) not in noref["text_prompt"], "无参考音无前缀")
    ok("references" not in noref, "无参考音不带 references 字段")

    print("== 2. TAR 归档解包 ==")
    tar = make_tar({"readme.txt": b"hello", "audio.mp3": FAKE_MP3})
    ok(mx.looks_like_tar(tar), "ustar 识别")
    ok(mx.pick_audio_from_tar(tar) == FAKE_MP3, "TAR 内音频成员提取")
    ok(mx.pick_audio_from_tar(FAKE_MP3) is None, "非 TAR 输入返回 None")

    print("== 3. Mock 网关：MiniMax 合成链路 ==")
    srv, url = start_server(MockMiniMax)
    mx.BASE = url
    buf = mx.t2a_sync("你好", "fem-01")
    ok(buf == FAKE_MP3, "t2a hex 解码得到原始 mp3 字节")
    MockMiniMax.behavior["mode"] = "bad_base_resp"
    try: mx.t2a_sync("x", "v"); ok(False, "base_resp!=0 报错")
    except mx.MiniMaxError as e: ok(True, f"base_resp!=0 报错（{str(e)[:24]}…）")
    MockMiniMax.behavior["mode"] = "http_error"
    try: mx.t2a_sync("x", "v"); ok(False, "HTML 错误页归一化")
    except mx.MiniMaxError: ok(True, "HTML 错误页归一化")
    MockMiniMax.behavior["mode"] = "empty_audio"
    try: mx.t2a_sync("x", "v"); ok(False, "空 audio 报错")
    except mx.MiniMaxError: ok(True, "空 audio 报错")

    print("== 4. Mock 网关：音色设计/克隆 ==")
    MockMiniMax.behavior["mode"] = "ok"
    MockMiniMax.behavior["design_flow"] = True
    vid, trial = mx.design_voice("低沉男声", "试听文本")
    MockMiniMax.behavior["design_flow"] = False
    ok(vid.startswith("pd-") and trial == FAKE_MP3, "voice_design 返回持久 voice_id+试听字节")
    try: mx.design_voice("", "x"); ok(False, "空描述拒绝")
    except mx.MiniMaxError: ok(True, "空描述拒绝")
    MockMiniMax.behavior["mode"] = "sensitive"
    try: mx.clone_voice(FAKE_MP3, "podcast-v01-aaaa"); ok(False, "input_sensitive 拦截")
    except mx.MiniMaxError as e: ok(True, f"input_sensitive 拦截（{str(e)[:20]}…）")
    MockMiniMax.behavior["mode"] = "ok"
    rid = mx.clone_voice(FAKE_MP3, "podcast-v01-bbbb")
    ok(rid == "podcast-v01-bbbb", "克隆成功返回 voice_id")

    print("== 5. Mock 网关：豆包链路 ==")
    srv2, url2 = start_server(MockDoubao)
    db.BASE = url2
    os.environ["DOUBAO_API_KEY"] = "k-test"
    wav, sub = db.create_speech("你好", [base64.b64encode(FAKE_MP3).decode()], "")
    ok(wav.startswith(b"RIFF"), "豆包 base64 解码音频")
    ok(sub is not None and "text" in sub, "豆包字幕透传")
    ok(MockDoubao.behavior["last_req"]["references"][0]["audio_data"].startswith("w==") is False,
       "豆包请求 references 非空")
    MockDoubao.behavior["mode"] = "bad_code"
    try: db.create_speech("x"); ok(False, "code!=0 报错")
    except db.DoubaoError as e: ok(True, f"code!=0 报错（{str(e)[:24]}…）")
    MockDoubao.behavior["mode"] = "empty_audio"
    try: db.create_speech("x"); ok(False, "豆包空 audio 报错")
    except db.DoubaoError: ok(True, "豆包空 audio 报错")
    os.environ["DOUBAO_API_KEY"] = "wrong"
    try: db.create_speech("x"); ok(False, "错 Key 被拒")
    except db.DoubaoError: ok(True, "错 Key 被拒")
    os.environ["DOUBAO_API_KEY"] = "k-test"

    print("== 6. 引擎级 synthesize（逐轮+轮缓存）==")
    os.environ["DOUBAO_API_KEY"] = "k-test"
    MockDoubao.behavior["mode"] = "ok"
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), "podtest_voice.wav")
    open(tmp, "wb").write(FAKE_MP3)
    script = {"speakers": [
        {"id": "S1", "prompt_audio": tmp},
        {"id": "S2", "prompt_audio": tmp}],
        "text": [["S1", f"第一轮 unique{time.time_ns()}。"], ["S2", f"第二轮 unique{time.time_ns()}。"]],
        "meta": {"job_id": "mockjob"}}
    out = os.path.join(os.environ.get("TEMP", "/tmp"), "podtest_out.wav")
    MockDoubao.behavior["calls"] = 0
    meta = db.synthesize(script, out)
    ok(meta["segments"] == 2 and meta["engine"] == "doubao", "豆包引擎 2 轮合成返回 meta")
    ok(MockDoubao.behavior["calls"] == 2, "首轮 2 次真实 Mock 调用")
    meta2 = db.synthesize(script, out)
    ok(meta2["duration_sec"] > 0, "重合成产物可探测时长")
    ok(MockDoubao.behavior["calls"] == 2, "轮缓存命中：重合成 0 次新调用")

    print("== 7. 音色×引擎亲和 ==")
    ok("soulx" in voice_store.supported_engines(voice_store.get("v01_female_warm")),
       "本地参考音音色支持 soulx")
    ok("doubao" in voice_store.supported_engines(voice_store.get("v01_female_warm")),
       "本地参考音音色支持 doubao（零样本）")
    ok("minimax" in voice_store.supported_engines(voice_store.get("v01_female_warm")),
       "未复刻音色可回落 MiniMax 系统默认音色")
    fake = {"id": "vx", "name": "x", "gender": "female", "age_range": "1",
            "style_tags": [], "prompt_audio": "voice_lib/audio/v01_female_warm.wav",
            "prompt_text": "", "engines": {"minimax": {"voice_id": "vd_12345678"}}}
    ok("minimax" in voice_store.supported_engines(fake), "登记 minimax 后支持")

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    srv.shutdown(); srv2.shutdown()
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
