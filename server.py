import http.server
import socketserver
import socket
import urllib.parse
import json
import time
import os

PORT = 8000
STATE_FILE = "scoreboard_state.json"
PRESETS_FILE = "team_presets.json"

PERIODS_STD = ["1ST", "2ND", "3RD", "4TH", "OT", "2OT"]
PERIODS_SOCCER = ["1ST HALF", "2ND HALF", "ET 1", "ET 2", "PENS"]

def get_local_ipv4():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not send packets; asks OS which local adapter routes outbound
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def load_state_dict():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}

def save_state_dict(state):
    state["updatedAt"] = int(time.time() * 1000)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return state

def get_effective_clock_ms(state):
    clock_ms = int(state.get("clockMs", 0))
    if not state.get("clockRunning") or not state.get("lastTickAt"):
        return clock_ms
    elapsed = int(time.time() * 1000) - int(state["lastTickAt"])
    if state.get("clockCountUp"):
        return clock_ms + max(0, elapsed)
    return max(0, clock_ms - max(0, elapsed))

def execute_command(qs):
    action = (qs.get("action", [""])[0] or "").strip().lower()
    side = (qs.get("side", [""])[0] or "").strip().lower()
    val_str = (qs.get("val", [""])[0] or "").strip()
    view = (qs.get("view", [""])[0] or "").strip().lower()

    try:
        val = int(val_str) if val_str else 0
    except ValueError:
        val = 0

    state = load_state_dict()
    if not state:
        return False, {"ok": False, "error": "State not initialized yet. Open the scoreboard controller once in a browser first."}

    now_ms = int(time.time() * 1000)

    # 1. CLOCK & PERIOD COMMANDS
    if action == "clock_toggle":
        if state.get("clockRunning"):
            state["clockMs"] = get_effective_clock_ms(state)
            state["clockRunning"] = False
            state["lastTickAt"] = None
        elif state.get("clockCountUp") or int(state.get("clockMs", 0)) > 0:
            state["clockRunning"] = True
            state["lastTickAt"] = now_ms

    elif action == "clock_start":
        if not state.get("clockRunning") and (state.get("clockCountUp") or int(state.get("clockMs", 0)) > 0):
            state["clockRunning"] = True
            state["lastTickAt"] = now_ms

    elif action == "clock_stop":
        if state.get("clockRunning"):
            state["clockMs"] = get_effective_clock_ms(state)
            state["clockRunning"] = False
            state["lastTickAt"] = None

    elif action == "clock_nudge":
        state["clockMs"] = max(0, get_effective_clock_ms(state) + (val * 1000))
        if state.get("clockRunning"):
            state["lastTickAt"] = now_ms

    elif action == "clock_set":
        state["clockRunning"] = False
        state["lastTickAt"] = None
        state["clockMs"] = max(0, val) * 60000
        state["shotSec"] = state.get("shotMax", 30)

    elif action == "period_next":
        plist = PERIODS_SOCCER if state.get("sport") == "soccer" else PERIODS_STD
        state["periodIdx"] = (int(state.get("periodIdx", 0)) + 1) % len(plist)

    elif action == "period_prev":
        plist = PERIODS_SOCCER if state.get("sport") == "soccer" else PERIODS_STD
        state["periodIdx"] = (int(state.get("periodIdx", 0)) - 1 + len(plist)) % len(plist)

    elif action == "shot_reset":
        sec = val if val > 0 else int(state.get("shotMax", 30))
        state["shotMax"] = sec
        state["shotSec"] = sec

    elif action == "shot_toggle":
        state["showShot"] = not bool(state.get("showShot", False))

    # 2. SCORING, FOULS, TIMEOUTS & STATS
    elif action == "score" and side in ("away", "home"):
        team = state.setdefault(side, {})
        team["score"] = max(0, int(team.get("score", 0)) + val)
        if state.get("sport") == "volleyball" and val > 0:
            state["possession"] = side

    elif action == "to" and side in ("away", "home"):
        team = state.setdefault(side, {})
        max_to = int(team.get("maxTo", 5))
        team["timeouts"] = min(max_to, max(0, int(team.get("timeouts", 0)) + val))

    elif action == "foul" and side in ("away", "home"):
        team = state.setdefault(side, {})
        team["fouls"] = max(0, int(team.get("fouls", 0)) + val)

    elif action == "sog" and side in ("away", "home"):
        team = state.setdefault(side, {})
        team["sog"] = max(0, int(team.get("sog", 0)) + val)

    # 3. FOOTBALL SITUATION COMMANDS
    elif action == "first_and_ten":
        state["down"] = 1
        state["distance"] = 10
        state["goalToGo"] = False

    elif action == "down_next":
        state["down"] = (int(state.get("down", 1)) % 4) + 1

    elif action == "dist_adj":
        state["distance"] = max(1, int(state.get("distance", 10)) + val)
        state["goalToGo"] = False

    elif action == "goal_to_go":
        state["goalToGo"] = not bool(state.get("goalToGo", False))

    elif action == "flag_toggle":
        state["flag"] = not bool(state.get("flag", False))

    elif action == "poss" and side in ("away", "home", "none"):
        state["possession"] = "" if side == "none" else side

    # 4. SOCCER & LACROSSE COMMANDS
    elif action == "stoppage_adj":
        state["stoppageMin"] = max(0, int(state.get("stoppageMin", 0)) + val)

    elif action == "yellow_card" and side in ("away", "home"):
        team = state.setdefault(side, {})
        team["yellowCards"] = max(0, int(team.get("yellowCards", 0)) + (val if val != 0 else 1))

    elif action == "red_card" and side in ("away", "home"):
        team = state.setdefault(side, {})
        team["redCards"] = max(0, int(team.get("redCards", 0)) + (val if val != 0 else 1))

    elif action == "penalty" and side in ("away", "home"):
        team = state.setdefault(side, {})
        team["penaltySec"] = max(0, val)

    # 5. BASEBALL COMMANDS
    elif action == "bb_ball":
        balls = int(state.get("balls", 0)) + 1
        if balls >= 4:
            state["balls"] = 0
            state["strikes"] = 0
        else:
            state["balls"] = balls

    elif action == "bb_strike":
        strikes = int(state.get("strikes", 0)) + 1
        if strikes >= 3:
            state["balls"] = 0
            state["strikes"] = 0
            outs = int(state.get("outs", 0)) + 1
            if outs >= 3:
                state["outs"] = 0
                state["bases"] = {"b1": False, "b2": False, "b3": False}
                if state.get("inningHalf", "TOP") == "TOP":
                    state["inningHalf"] = "BOT"
                else:
                    state["inningHalf"] = "TOP"
                    state["inning"] = int(state.get("inning", 1)) + 1
            else:
                state["outs"] = outs
        else:
            state["strikes"] = strikes

    elif action == "bb_out":
        state["balls"] = 0
        state["strikes"] = 0
        outs = int(state.get("outs", 0)) + 1
        if outs >= 3:
            state["outs"] = 0
            state["bases"] = {"b1": False, "b2": False, "b3": False}
            if state.get("inningHalf", "TOP") == "TOP":
                state["inningHalf"] = "BOT"
            else:
                state["inningHalf"] = "TOP"
                state["inning"] = int(state.get("inning", 1)) + 1
        else:
            state["outs"] = outs

    elif action == "bb_clear_count":
        state["balls"] = 0
        state["strikes"] = 0

    elif action == "bb_base" and val in (1, 2, 3):
        bases = state.setdefault("bases", {"b1": False, "b2": False, "b3": False})
        key = f"b{val}"
        bases[key] = not bool(bases.get(key, False))

    elif action == "bb_clear_bases":
        state["bases"] = {"b1": False, "b2": False, "b3": False}

    # 6. BROADCAST GRAPHIC CARDS & LOWER-THIRD TICKER
    elif action == "card":
        target = view or val_str.lower()
        if target in ("none", "matchup", "lineups", "keys", "stats"):
            current = state.get("activeCard", "none")
            state["activeCard"] = "none" if (current == target and target != "none") else target
        else:
            return False, {"ok": False, "error": f"Unknown card view: {target}"}

    elif action == "ticker_toggle":
        state["showTicker"] = not bool(state.get("showTicker", False))

    elif action == "ticker_on":
        state["showTicker"] = True

    elif action == "ticker_off":
        state["showTicker"] = False

    else:
        return False, {"ok": False, "error": f"Unknown or incomplete command: {action}"}

    save_state_dict(state)
    return True, {"ok": True, "action": action, "updatedAt": state["updatedAt"]}

class ScoreboardHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/state":
            self._serve_json(STATE_FILE, b"{}")
            return
        if parsed.path == "/api/presets":
            self._serve_json(PRESETS_FILE, b"[]")
            return
        if parsed.path == "/api/cmd":
            qs = urllib.parse.parse_qs(parsed.query)
            ok, payload = execute_command(qs)
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return
        if parsed.path == "/api/info":
            ip = get_local_ipv4()
            payload = json.dumps({
                "ip": ip,
                "port": PORT,
                "tablet_url": f"http://{ip}:{PORT}/obs_multisport_scoreboard.html?mode=control",
                "obs_url": f"http://127.0.0.1:{PORT}/obs_multisport_scoreboard.html?mode=overlay",
                "cmd_base_local": f"http://127.0.0.1:{PORT}/api/cmd",
                "cmd_base_lan": f"http://{ip}:{PORT}/api/cmd"
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/state":
            self._save_json(STATE_FILE)
            return
        if parsed.path == "/api/presets":
            self._save_json(PRESETS_FILE)
            return
        if parsed.path == "/api/cmd":
            qs = urllib.parse.parse_qs(parsed.query)
            ok, payload = execute_command(qs)
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return
        self.send_error(404)

    def _serve_json(self, filename, default_bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if os.path.exists(filename):
            with open(filename, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.wfile.write(default_bytes)

    def _save_json(self, filename):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        with open(filename, "wb") as f:
            f.write(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, format, *args):
        pass

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    local_ip = get_local_ipv4()
    print("============================================================")
    print(f" Multi-Sport Broadcast Suite running on Port {PORT}")
    print(f" Tablet Controller URL: http://{local_ip}:{PORT}/obs_multisport_scoreboard.html?mode=control")
    print(f" OBS Overlay URL:       http://127.0.0.1:{PORT}/obs_multisport_scoreboard.html?mode=overlay")
    print(f" Stream Deck API Base:  http://127.0.0.1:{PORT}/api/cmd?action=...")
    print(f" Touch Portal API Base: http://{local_ip}:{PORT}/api/cmd?action=...")
    print("============================================================")
    print("Keep this window minimized while broadcasting.")
    with ReusableTCPServer(("0.0.0.0", PORT), ScoreboardHandler) as httpd:
        httpd.serve_forever()
