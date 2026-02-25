from flask import Flask, request, jsonify, render_template
import time, random, os, threading

app = Flask(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
ROUND_TIMEOUT      = 30   # seconds before auto-advancing if not all agents answered
KICK_AFTER_MISSED  = 2    # kick agent after missing this many rounds in a row

QUESTIONS = [
    {"q": "What planet is known as the Red Planet?",          "a": "mars"},
    {"q": "How many sides does a hexagon have?",              "a": "6"},
    {"q": "What is the chemical symbol for gold?",            "a": "au"},
    {"q": "Who wrote Romeo and Juliet?",                      "a": "shakespeare"},
    {"q": "What is the largest ocean on Earth?",              "a": "pacific"},
    {"q": "What is 12 × 12?",                                 "a": "144"},
    {"q": "What gas do plants absorb from the atmosphere?",   "a": "carbon dioxide"},
    {"q": "What is the capital of France?",                   "a": "paris"},
    {"q": "How many bones are in the adult human body?",      "a": "206"},
    {"q": "What language has the most native speakers?",      "a": "mandarin"},
    {"q": "What element has atomic number 1?",                "a": "hydrogen"},
    {"q": "What year did World War II end?",                  "a": "1945"},
    {"q": "How many continents are there?",                   "a": "7"},
    {"q": "What is the speed of light in km/s (approx)?",    "a": "300000"},
    {"q": "What animal is the tallest in the world?",         "a": "giraffe"},
    {"q": "What is the square root of 144?",                  "a": "12"},
    {"q": "Which planet has the most moons?",                 "a": "saturn"},
    {"q": "What is the hardest natural substance on Earth?",  "a": "diamond"},
    {"q": "How many degrees in a right angle?",               "a": "90"},
    {"q": "What is the currency of Japan?",                   "a": "yen"},
]

# ── State ────────────────────────────────────────────────────────────────────
agents  = {}   # name -> {name, score, missed_rounds, last_active, status}
answers = {}   # name -> {answer, correct, ts}
log     = []   # event log shown in frontend
lock    = threading.Lock()

game = {
    "round":      1,
    "question":   random.choice(QUESTIONS),
    "phase":      "open",
    "started_at": time.time(),
}

# ── Helpers ──────────────────────────────────────────────────────────────────
def push_log(event_type, **kwargs):
    entry = {"type": event_type, "ts": time.time(), **kwargs}
    log.append(entry)
    if len(log) > 300:
        log.pop(0)

def active_agents():
    return {n: a for n, a in agents.items() if a["status"] == "active"}

def kick_inactive():
    to_kick = [
        name for name, a in agents.items()
        if a["status"] == "active" and a["missed_rounds"] >= KICK_AFTER_MISSED
    ]
    for name in to_kick:
        agents[name]["status"] = "idle"
        push_log("kick", agent=name,
                 msg=f"{name} removed from active play after {KICK_AFTER_MISSED} missed rounds")

def advance_round(reason="all_answered"):
    global game, answers
    correct_answer = game["question"]["a"]

    for name in active_agents():
        if name not in answers:
            agents[name]["missed_rounds"] += 1
        else:
            agents[name]["missed_rounds"] = 0

    push_log("round_end",
             round=game["round"],
             correct_answer=correct_answer,
             reason=reason,
             scores={n: a["score"] for n, a in agents.items() if a["status"] == "active"})

    kick_inactive()

    answers            = {}
    game["round"]      += 1
    game["question"]   = random.choice(QUESTIONS)
    game["phase"]      = "open"
    game["started_at"] = time.time()

    push_log("new_round", round=game["round"], question=game["question"]["q"])

# ── Background timeout thread ─────────────────────────────────────────────────
def timeout_watcher():
    while True:
        time.sleep(5)
        with lock:
            if game["phase"] == "open":
                elapsed = time.time() - game["started_at"]
                if elapsed >= ROUND_TIMEOUT and len(active_agents()) > 0:
                    game["phase"] = "scoring"
                    push_log("timeout", round=game["round"],
                             msg=f"⏱ Round {game['round']} timed out after {ROUND_TIMEOUT}s")
                    advance_round(reason="timeout")

threading.Thread(target=timeout_watcher, daemon=True).start()
push_log("system", msg="⚡ Claw Trivia Duel server started. Waiting for agents...")

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/join", methods=["POST"])
def join():
    data = request.get_json(force=True)
    name = str(data.get("agent_name", "")).strip()
    if not name:
        return jsonify({"error": "agent_name required"}), 400

    with lock:
        if name not in agents:
            agents[name] = {"name": name, "score": 0, "missed_rounds": 0,
                            "last_active": time.time(), "status": "active"}
            push_log("join", agent=name)
        else:
            agents[name]["status"]        = "active"
            agents[name]["missed_rounds"] = 0
            agents[name]["last_active"]   = time.time()
            push_log("rejoin", agent=name)
        q, r = game["question"]["q"], game["round"]

    return jsonify({"status": "ok", "message": f"Welcome, {name}!",
                    "current_round": r, "question": q,
                    "round_timeout": ROUND_TIMEOUT})

@app.route("/leave", methods=["POST"])
def leave():
    data = request.get_json(force=True)
    name = str(data.get("agent_name", "")).strip()
    if not name or name not in agents:
        return jsonify({"error": "Unknown agent"}), 404
    with lock:
        agents[name]["status"] = "idle"
        push_log("leave", agent=name)
    return jsonify({"status": "ok", "message": f"{name} marked as inactive."})

@app.route("/question", methods=["GET"])
def get_question():
    with lock:
        return jsonify({
            "round":        game["round"],
            "phase":        game["phase"],
            "question":     game["question"]["q"],
            "seconds_left": max(0, round(ROUND_TIMEOUT - (time.time() - game["started_at"]), 1)),
        })

@app.route("/answer", methods=["POST"])
def submit_answer():
    data       = request.get_json(force=True)
    name       = str(data.get("agent_name", "")).strip()
    raw_answer = str(data.get("answer", "")).strip()

    if not name or not raw_answer:
        return jsonify({"error": "agent_name and answer required"}), 400

    with lock:
        if name not in agents:
            return jsonify({"error": "Agent not registered. POST /join first."}), 403
        if agents[name]["status"] != "active":
            agents[name]["status"]        = "active"
            agents[name]["missed_rounds"] = 0
        if game["phase"] != "open":
            return jsonify({"status": "late", "message": "Round already scored. Wait for next round."}), 200
        if name in answers:
            return jsonify({"status": "already_answered", "message": "You already answered this round."}), 200

        correct_raw = game["question"]["a"]
        is_correct  = raw_answer.lower().strip() == correct_raw.lower().strip()

        answers[name]                 = {"answer": raw_answer, "correct": is_correct, "ts": time.time()}
        agents[name]["last_active"]   = time.time()
        agents[name]["missed_rounds"] = 0

        if is_correct:
            agents[name]["score"] += 1

        push_log("answer", agent=name, answer=raw_answer, correct=is_correct, round=game["round"])

        my_score = agents[name]["score"]
        active   = active_agents()
        if len(active) > 0 and all(n in answers for n in active):
            game["phase"] = "scoring"
            advance_round(reason="all_answered")

    return jsonify({"status": "received", "correct": is_correct, "your_score": my_score,
                    "message": "Correct! +1 point 🎉" if is_correct else f"Wrong. The answer was: {correct_raw}"})

@app.route("/scores", methods=["GET"])
def scores():
    with lock:
        board = sorted(agents.values(), key=lambda x: x["score"], reverse=True)
        return jsonify({"scores": list(board), "round": game["round"]})

@app.route("/feed", methods=["GET"])
def feed():
    since = float(request.args.get("since", 0))
    with lock:
        events = [e for e in log if e["ts"] > since]
        return jsonify({
            "events":       events,
            "round":        game["round"],
            "question":     game["question"]["q"],
            "phase":        game["phase"],
            "seconds_left": max(0, round(ROUND_TIMEOUT - (time.time() - game["started_at"]), 1)),
            "scores":       sorted(agents.values(), key=lambda x: x["score"], reverse=True),
            "active_count": len(active_agents()),
        })

@app.route("/next_round", methods=["POST"])
def next_round():
    with lock:
        advance_round(reason="manual")
    return jsonify({"status": "ok", "round": game["round"], "question": game["question"]["q"]})

@app.route("/reset", methods=["POST"])
def reset():
    global agents, answers, log, game
    with lock:
        agents  = {}
        answers = {}
        log     = []
        game    = {"round": 1, "question": random.choice(QUESTIONS),
                   "phase": "open", "started_at": time.time()}
        push_log("system", msg="🔄 Game reset.")
    return jsonify({"status": "ok", "message": "Game reset."})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)