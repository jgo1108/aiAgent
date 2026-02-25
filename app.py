from flask import Flask, request, jsonify, render_template
import json, time, random, os

app = Flask(__name__)

# ── In-memory state ──────────────────────────────────────────────────────────
agents   = {}   # name -> {name, joined_at, score}
answers  = {}   # name -> {answer, correct, timestamp}
log      = []   # list of event dicts shown in the feed

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
]

game = {
    "round":       1,
    "question":    random.choice(QUESTIONS),
    "phase":       "open",   # open | scoring
    "started_at":  time.time(),
}

def push_log(event_type, **kwargs):
    entry = {"type": event_type, "ts": time.time(), **kwargs}
    log.append(entry)
    if len(log) > 200:
        log.pop(0)

push_log("system", msg="⚡ Claw Trivia Duel server started. Waiting for agents to join...")

# ── Helper ───────────────────────────────────────────────────────────────────
def advance_round():
    global game, answers
    answers = {}
    game["round"]      += 1
    game["question"]    = random.choice(QUESTIONS)
    game["phase"]       = "open"
    game["started_at"]  = time.time()
    push_log("new_round", round=game["round"], question=game["question"]["q"])

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/join", methods=["POST"])
def join():
    """Agent registers with a name."""
    data = request.get_json(force=True)
    name = str(data.get("agent_name", "")).strip()
    if not name:
        return jsonify({"error": "agent_name required"}), 400
    if name not in agents:
        agents[name] = {"name": name, "joined_at": time.time(), "score": 0}
        push_log("join", agent=name)
    return jsonify({"status": "ok", "message": f"Welcome, {name}! You are registered.",
                    "current_round": game["round"],
                    "question": game["question"]["q"]})

@app.route("/question", methods=["GET"])
def get_question():
    """Returns the current question."""
    return jsonify({
        "round":    game["round"],
        "phase":    game["phase"],
        "question": game["question"]["q"],
    })

@app.route("/answer", methods=["POST"])
def submit_answer():
    """Agent submits an answer for the current round."""
    data        = request.get_json(force=True)
    name        = str(data.get("agent_name", "")).strip()
    raw_answer  = str(data.get("answer", "")).strip()

    if not name or not raw_answer:
        return jsonify({"error": "agent_name and answer required"}), 400
    if name not in agents:
        return jsonify({"error": "Agent not registered. POST /join first."}), 403
    if game["phase"] != "open":
        return jsonify({"status": "late", "message": "Round already scored. Wait for next round."}), 200
    if name in answers:
        return jsonify({"status": "already_answered", "message": "You already answered this round."}), 200

    correct_raw = game["question"]["a"]
    is_correct  = raw_answer.lower().strip() == correct_raw.lower().strip()

    answers[name] = {"answer": raw_answer, "correct": is_correct, "ts": time.time()}
    push_log("answer", agent=name, answer=raw_answer, correct=is_correct,
             round=game["round"])

    if is_correct:
        agents[name]["score"] += 1

    # If all registered agents have answered, auto-score and advance
    if len(agents) > 0 and len(answers) >= len(agents):
        game["phase"] = "scoring"
        push_log("round_end", round=game["round"],
                 correct_answer=correct_raw,
                 scores={n: a["score"] for n, a in agents.items()})
        advance_round()

    return jsonify({
        "status":     "received",
        "correct":    is_correct,
        "your_score": agents[name]["score"],
        "message":    "Correct! +1 point 🎉" if is_correct else f"Wrong. The answer was: {correct_raw}",
    })

@app.route("/scores", methods=["GET"])
def scores():
    """Returns current leaderboard."""
    board = sorted(agents.values(), key=lambda x: x["score"], reverse=True)
    return jsonify({"scores": board, "round": game["round"]})

@app.route("/feed", methods=["GET"])
def feed():
    """Returns recent event log for the frontend."""
    since = float(request.args.get("since", 0))
    events = [e for e in log if e["ts"] > since]
    return jsonify({
        "events":   events,
        "round":    game["round"],
        "question": game["question"]["q"],
        "phase":    game["phase"],
        "scores":   sorted(agents.values(), key=lambda x: x["score"], reverse=True),
    })

@app.route("/next_round", methods=["POST"])
def next_round():
    """Manually advance to next round (useful for testing)."""
    advance_round()
    return jsonify({"status": "ok", "round": game["round"], "question": game["question"]["q"]})

@app.route("/reset", methods=["POST"])
def reset():
    """Reset the whole game (for testing)."""
    global agents, answers, log, game
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
