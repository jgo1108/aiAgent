from flask import Flask, request, jsonify, render_template
import time, random, os, threading

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
ROUND_TIMEOUT     = 10   # seconds before auto-advancing
KICK_AFTER_MISSED = 3    # kick after missing this many rounds
CHAMPION_TIMEOUT  = 300  # seconds before Champion tier resets agent to Gold

TIERS = ["Bronze", "Silver", "Gold", "Diamond", "Champion"]
STARTING_TIER_IDX = 0    # everyone starts at Bronze
TIER_EMOJI = {
    "Bronze":   "🥉",
    "Silver":   "🥈",
    "Gold":     "🥇",
    "Diamond":  "💎",
    "Champion": "👑",
}

# ── Question Bank (tiered by difficulty) ─────────────────────────────────────
QUESTIONS_BY_TIER = {
    "Bronze": [
        {"q": "What planet is known as the Red Planet?",         "a": "mars"},
        {"q": "How many sides does a hexagon have?",             "a": "6"},
        {"q": "What is the capital of France?",                  "a": "paris"},
        {"q": "What animal is the tallest in the world?",        "a": "giraffe"},
        {"q": "How many degrees are in a right angle?",          "a": "90"},
        {"q": "What is the chemical formula for water?",         "a": "h2o"},
        {"q": "What is 7 × 8?",                                  "a": "56"},
        {"q": "How many days are in a week?",                    "a": "7"},
        {"q": "What is the largest continent?",                  "a": "asia"},
        {"q": "What color is a ripe banana?",                    "a": "yellow"},
    ],
    "Silver": [
        {"q": "What is the chemical symbol for gold?",           "a": "au"},
        {"q": "What year did World War II end?",                 "a": "1945"},
        {"q": "What element has atomic number 1?",               "a": "hydrogen"},
        {"q": "What is the square root of 144?",                 "a": "12"},
        {"q": "What is the largest ocean on Earth?",             "a": "pacific"},
        {"q": "How many bones in the adult human body?",         "a": "206"},
        {"q": "What gas do plants absorb from the atmosphere?",  "a": "carbon dioxide"},
        {"q": "What language has the most native speakers?",     "a": "mandarin"},
        {"q": "What is the currency of Japan?",                  "a": "yen"},
        {"q": "Who wrote Romeo and Juliet?",                     "a": "shakespeare"},
    ],
    "Gold": [
        {"q": "What is the approximate speed of light in km/s?", "a": "300000"},
        {"q": "Which planet has the most moons?",                "a": "saturn"},
        {"q": "What is the hardest natural substance on Earth?", "a": "diamond"},
        {"q": "What is the boiling point of water in Fahrenheit?","a": "212"},
        {"q": "What is the largest planet in our solar system?", "a": "jupiter"},
        {"q": "In what year was the Eiffel Tower completed?",    "a": "1889"},
        {"q": "What is the powerhouse of the cell?",             "a": "mitochondria"},
        {"q": "What is the chemical formula for table salt?",    "a": "nacl"},
        {"q": "Who painted the Mona Lisa?",                      "a": "da vinci"},
        {"q": "What is 12 × 12?",                                "a": "144"},
    ],
    "Diamond": [
        {"q": "What is the atomic number of carbon?",            "a": "6"},
        {"q": "Who wrote War and Peace?",                        "a": "tolstoy"},
        {"q": "What is the smallest prime number?",              "a": "2"},
        {"q": "In what year did the French Revolution begin?",   "a": "1789"},
        {"q": "What is the chemical symbol for iron?",           "a": "fe"},
        {"q": "What is the longest river in the world?",         "a": "nile"},
        {"q": "Who invented the telephone?",                     "a": "bell"},
        {"q": "What is the capital of Australia?",               "a": "canberra"},
        {"q": "What is pi to 2 decimal places?",                 "a": "3.14"},
        {"q": "What is the chemical formula for carbon dioxide?","a": "co2"},
    ],
    "Champion": [
        {"q": "What is the half-life of Carbon-14 in years?",    "a": "5730"},
        {"q": "Who discovered penicillin?",                      "a": "fleming"},
        {"q": "What is the speed of sound in m/s at 20°C?",      "a": "343"},
        {"q": "Who developed the theory of general relativity?", "a": "einstein"},
        {"q": "In what year was the Magna Carta signed?",        "a": "1215"},
        {"q": "What is the chemical formula for glucose?",       "a": "c6h12o6"},
        {"q": "What element has the highest electronegativity?", "a": "fluorine"},
        {"q": "What is the capital of Kazakhstan?",              "a": "astana"},
        {"q": "What is the smallest bone in the human body?",    "a": "stapes"},
        {"q": "Who wrote Ulysses?",                              "a": "joyce"},
    ],
}

# ── State ─────────────────────────────────────────────────────────────────────
agents        = {}   # name -> {name, tier, wins, losses, missed_rounds, status}
matches       = []   # active matches this round
match_history = []   # completed matches
log           = []   # event log
lock          = threading.Lock()

tournament = {"round": 1, "phase": "waiting", "started_at": time.time()}

# ── Helpers ───────────────────────────────────────────────────────────────────
def push_log(event_type, **kwargs):
    entry = {"type": event_type, "ts": time.time(), **kwargs}
    log.append(entry)
    if len(log) > 500:
        log.pop(0)

def active_agents():
    return {n: a for n, a in agents.items() if a["status"] == "active"}

def get_agent_match(name):
    for m in matches:
        if m["agent1"] == name or m["agent2"] == name:
            return m
    return None

def resolve_match(m):
    a1, a2 = m["agent1"], m["agent2"]
    if a2 is None:
        m["result"] = {"winner": a1, "loser": None, "reason": "bye"}
        return
    ans = m["answers"]
    a1c = ans.get(a1, {}).get("correct", False)
    a2c = ans.get(a2, {}).get("correct", False)
    if a1c and a2c:
        winner, loser = (a1, a2) if ans[a1]["ts"] <= ans[a2]["ts"] else (a2, a1)
        reason = "speed"
    elif a1c:
        winner, loser, reason = a1, a2, "correct"
    elif a2c:
        winner, loser, reason = a2, a1, "correct"
    else:
        m["result"] = {"winner": None, "loser": None, "reason": "tie"}
        return
    m["result"] = {"winner": winner, "loser": loser, "reason": reason}

def apply_bracket_moves(m):
    result = m["result"]
    if not result:
        return
    winner, loser = result.get("winner"), result.get("loser")
    if winner and winner in agents:
        agents[winner]["wins"] += 1
        agents[winner]["consecutive_ties"] = 0
        idx = TIERS.index(agents[winner]["tier"])
        if idx < len(TIERS) - 1:
            new_tier = TIERS[idx + 1]
            agents[winner]["tier"] = new_tier
            if new_tier == "Champion":
                agents[winner]["champion_since"] = time.time()
            push_log("bracket_up", agent=winner, from_tier=TIERS[idx], to_tier=new_tier,
                     msg=f"{TIER_EMOJI[new_tier]} {winner} promoted to {new_tier}!")
        else:
            push_log("champion_holds", agent=winner, tier="Champion",
                     msg=f"👑 {winner} defends the Champion tier!")
    if loser and loser in agents:
        agents[loser]["losses"] += 1
        agents[loser]["consecutive_ties"] = 0
        idx = TIERS.index(agents[loser]["tier"])
        if idx > 0:
            new_tier = TIERS[idx - 1]
            agents[loser]["tier"] = new_tier
            if TIERS[idx] == "Champion":
                agents[loser]["champion_since"] = None
            push_log("bracket_down", agent=loser, from_tier=TIERS[idx], to_tier=new_tier,
                     msg=f"⬇️ {loser} dropped to {new_tier}")
        else:
            push_log("bracket_floor", agent=loser, tier="Bronze",
                     msg=f"🥉 {loser} stays at the Bronze floor")
    # Tie handling — pity promotion after 3 consecutive ties at Bronze
    if not winner and not loser:
        for name in [m["agent1"], m["agent2"]]:
            if name and name in agents:
                a = agents[name]
                if a["tier"] == "Bronze":
                    a["consecutive_ties"] = a.get("consecutive_ties", 0) + 1
                    if a["consecutive_ties"] >= 3:
                        a["tier"] = TIERS[1]  # Silver
                        a["consecutive_ties"] = 0
                        push_log("pity_promotion", agent=name, from_tier="Bronze", to_tier="Silver",
                                 msg=f"🤝 {name} pity-promoted to Silver after 3 consecutive ties!")

def create_round_matches():
    global matches
    new_matches = []
    for tier in TIERS:
        tier_agents = [n for n, a in agents.items()
                       if a["status"] == "active" and a["tier"] == tier]
        random.shuffle(tier_agents)
        for i in range(0, len(tier_agents) - 1, 2):
            a1, a2 = tier_agents[i], tier_agents[i + 1]
            q = random.choice(QUESTIONS_BY_TIER[tier])
            new_matches.append({
                "id": f"r{tournament['round']}_{tier}_{i//2}",
                "tier": tier, "agent1": a1, "agent2": a2,
                "question": q, "answers": {}, "result": None,
                "started_at": time.time(),
            })
            push_log("match_created", tier=tier, agent1=a1, agent2=a2,
                     question=q["q"],
                     msg=f"{TIER_EMOJI[tier]} [{tier}] {a1} vs {a2}")
        if len(tier_agents) % 2 == 1:
            bye = tier_agents[-1]
            q = random.choice(QUESTIONS_BY_TIER[tier])
            new_matches.append({
                "id": f"r{tournament['round']}_{tier}_bye",
                "tier": tier, "agent1": bye, "agent2": None,
                "question": q, "answers": {},
                "result": {"winner": bye, "loser": None, "reason": "bye"},
                "started_at": time.time(),
            })
            push_log("bye", agent=bye, tier=tier,
                     msg=f"🎯 {bye} gets a bye in {tier} this round")
    matches = new_matches

def all_resolved():
    return len(matches) > 0 and all(m["result"] is not None for m in matches)

def advance_round(reason="all_resolved"):
    global matches
    # Resolve any timed-out matches and log their results
    for m in matches:
        if m["result"] is None:
            resolve_match(m)
            if m["result"]:
                w = m["result"].get("winner")
                correct_ans = m["question"]["a"]
                msg = (f"⏱ [{m['tier']}] {w} wins (timeout) | Answer: {correct_ans}"
                       if w else f"⏱ [{m['tier']}] Tie (timeout) | Answer: {correct_ans}")
                push_log("match_result", match_id=m["id"], tier=m["tier"],
                         winner=w, loser=m["result"].get("loser"),
                         correct_answer=correct_ans, msg=msg)
    # Track missed rounds
    answered = {n for m in matches for n in m["answers"]}
    for name, a in agents.items():
        if a["status"] != "active":
            continue
        m = get_agent_match(name)
        if m and m["agent2"] is not None:
            if name not in answered:
                a["missed_rounds"] += 1
            else:
                a["missed_rounds"] = 0
            if a["missed_rounds"] >= KICK_AFTER_MISSED:
                a["status"] = "idle"
                push_log("kick", agent=name,
                         msg=f"⚠️ {name} removed for inactivity")
    # Apply bracket moves and archive
    for m in matches:
        apply_bracket_moves(m)
        match_history.append(m)
        if len(match_history) > 1000:
            match_history.pop(0)
    push_log("round_end", round=tournament["round"], reason=reason,
             msg=f"⚡ Round {tournament['round']} complete! Brackets updated.")
    matches = []
    tournament["round"] += 1
    if len(active_agents()) >= 2:
        create_round_matches()
        tournament["phase"] = "open"
        tournament["started_at"] = time.time()
        push_log("new_round", round=tournament["round"],
                 msg=f"🔔 Round {tournament['round']} started!")
    else:
        tournament["phase"] = "waiting"
        push_log("system", msg="⏳ Waiting for more agents to join...")

# ── Background timeout watcher ────────────────────────────────────────────────
def timeout_watcher():
    while True:
        time.sleep(5)
        with lock:
            # Champion reign expiry
            now = time.time()
            for name, a in list(agents.items()):
                if (a["status"] == "active" and a["tier"] == "Champion"
                        and a.get("champion_since")
                        and (now - a["champion_since"]) >= CHAMPION_TIMEOUT):
                    a["tier"] = TIERS[STARTING_TIER_IDX]  # back to Gold
                    a["wins"] = 0
                    a["losses"] = 0
                    a["champion_since"] = None
                    push_log("champion_reset", agent=name,
                             msg=f"⏰ {name}'s reign ended after {CHAMPION_TIMEOUT//60}m — reset to {TIERS[STARTING_TIER_IDX]}!")
            # Round timeout
            if tournament["phase"] == "open":
                elapsed = time.time() - tournament["started_at"]
                if elapsed >= ROUND_TIMEOUT:
                    tournament["phase"] = "scoring"
                    push_log("timeout", round=tournament["round"],
                             msg=f"⏱ Round {tournament['round']} timed out after {ROUND_TIMEOUT}s")
                    advance_round(reason="timeout")

threading.Thread(target=timeout_watcher, daemon=True).start()
push_log("system", msg="⚡ Tournament server started. Waiting for agents...")

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
            agents[name] = {
                "name": name, "tier": TIERS[STARTING_TIER_IDX],
                "wins": 0, "losses": 0, "missed_rounds": 0, "status": "active",
                "champion_since": None, "consecutive_ties": 0,
            }
            push_log("join", agent=name, tier=TIERS[STARTING_TIER_IDX],
                     msg=f"🤖 {name} joined at {TIERS[STARTING_TIER_IDX]} bracket")
        else:
            agents[name]["status"] = "active"
            agents[name]["missed_rounds"] = 0
            push_log("rejoin", agent=name, tier=agents[name]["tier"],
                     msg=f"🔄 {name} rejoined ({agents[name]['tier']})")
        if tournament["phase"] == "waiting" and len(active_agents()) >= 2:
            create_round_matches()
            tournament["phase"] = "open"
            tournament["started_at"] = time.time()
            push_log("new_round", round=tournament["round"],
                     msg=f"🔔 Round {tournament['round']} started!")
        m = get_agent_match(name)
        a = agents[name]
        resp = {
            "status": "ok", "tier": a["tier"], "round": tournament["round"],
            "round_timeout": ROUND_TIMEOUT,
            "message": f"Welcome {name}! You are in the {a['tier']} bracket.",
        }
        if m:
            opp = m["agent2"] if m["agent1"] == name else m["agent1"]
            resp.update({"question": m["question"]["q"], "opponent": opp, "match_id": m["id"]})
        else:
            resp["question"] = "Waiting for match assignment next round..."
    return jsonify(resp)

@app.route("/leave", methods=["POST"])
def leave():
    data = request.get_json(force=True)
    name = str(data.get("agent_name", "")).strip()
    if not name or name not in agents:
        return jsonify({"error": "Unknown agent"}), 404
    with lock:
        agents[name]["status"] = "idle"
        push_log("leave", agent=name, msg=f"{name} left the tournament")
    return jsonify({"status": "ok", "message": f"{name} marked as inactive."})

@app.route("/question", methods=["GET"])
def get_question():
    name = request.args.get("agent_name", "").strip()
    with lock:
        if name and name in agents:
            m = get_agent_match(name)
            if m:
                opp = m["agent2"] if m["agent1"] == name else m["agent1"]
                return jsonify({
                    "round": tournament["round"], "phase": tournament["phase"],
                    "question": m["question"]["q"], "tier": m["tier"],
                    "match_id": m["id"], "opponent": opp,
                    "seconds_left": max(0, round(ROUND_TIMEOUT - (time.time() - m["started_at"]), 1)),
                })
        return jsonify({
            "round": tournament["round"], "phase": tournament["phase"],
            "question": "No active match this round.", "seconds_left": 0,
        })

@app.route("/answer", methods=["POST"])
def submit_answer():
    data = request.get_json(force=True)
    name = str(data.get("agent_name", "")).strip()
    raw  = str(data.get("answer", "")).strip()
    if not name or not raw:
        return jsonify({"error": "agent_name and answer required"}), 400
    with lock:
        if name not in agents:
            return jsonify({"error": "Not registered. POST /join first."}), 403
        if agents[name]["status"] != "active":
            agents[name]["status"] = "active"
            agents[name]["missed_rounds"] = 0
        if tournament["phase"] != "open":
            return jsonify({"status": "late", "message": "No open round. Wait for next round."}), 200
        m = get_agent_match(name)
        if not m:
            return jsonify({"status": "no_match", "message": "No match assigned this round."}), 200
        if m["result"] is not None:
            return jsonify({"status": "late", "message": "Match already resolved."}), 200
        if name in m["answers"]:
            return jsonify({"status": "already_answered"}), 200

        correct_raw = m["question"]["a"]
        is_correct  = raw.lower().strip() == correct_raw.lower().strip()
        m["answers"][name] = {"answer": raw, "correct": is_correct, "ts": time.time()}
        agents[name]["missed_rounds"] = 0
        push_log("answer", agent=name, answer=raw, correct=is_correct,
                 round=tournament["round"], tier=m["tier"])

        other = m["agent2"] if m["agent1"] == name else m["agent1"]
        if other is None or other in m["answers"]:
            resolve_match(m)
            w = m["result"].get("winner")
            correct_ans = m["question"]["a"]
            msg = (f"🏆 {w} wins the {m['tier']} match! Answer: {correct_ans}"
                   if w else f"🤝 {m['tier']} match tied! Answer: {correct_ans}")
            push_log("match_result", match_id=m["id"], tier=m["tier"],
                     winner=w, loser=m["result"].get("loser"),
                     correct_answer=correct_ans, msg=msg)

        if all_resolved():
            tournament["phase"] = "scoring"
            advance_round(reason="all_resolved")

    return jsonify({
        "status": "received", "correct": is_correct,
        "your_tier": agents[name]["tier"],
        "message": "Correct! ✓" if is_correct else f"Wrong. Answer: {correct_raw}",
    })

@app.route("/scores", methods=["GET"])
def scores():
    with lock:
        board = []
        for tier in reversed(TIERS):
            for a in sorted([x for x in agents.values() if x["tier"] == tier],
                            key=lambda x: (x["wins"], -x["losses"]), reverse=True):
                board.append(a)
        return jsonify({"scores": board, "round": tournament["round"],
                        "tiers": TIERS, "tier_emoji": TIER_EMOJI})

@app.route("/brackets", methods=["GET"])
def brackets():
    with lock:
        data = {}
        for tier in TIERS:
            tier_agents = sorted(
                [a for a in agents.values() if a["tier"] == tier],
                key=lambda x: (x["wins"], -x["losses"]), reverse=True,
            )
            tier_matches = [
                {"id": m["id"], "agent1": m["agent1"], "agent2": m["agent2"],
                 "result": m["result"], "answered": list(m["answers"].keys()),
                 "question": m["question"]["q"],
                 "correct_answer": m["question"]["a"] if m["result"] is not None else None}
                for m in matches if m["tier"] == tier
            ]
            data[tier] = {"tier": tier, "emoji": TIER_EMOJI[tier],
                          "agents": tier_agents, "matches": tier_matches}
        return jsonify({
            "round": tournament["round"], "phase": tournament["phase"],
            "brackets": data, "tiers": TIERS, "tier_emoji": TIER_EMOJI,
        })

@app.route("/feed", methods=["GET"])
def feed():
    since = float(request.args.get("since", 0))
    with lock:
        events = [e for e in log if e["ts"] > since]
        board = []
        for tier in reversed(TIERS):
            for a in sorted([x for x in agents.values() if x["tier"] == tier],
                            key=lambda x: (x["wins"], -x["losses"]), reverse=True):
                board.append(a)
        active_matches = [
            {"id": m["id"], "tier": m["tier"], "agent1": m["agent1"], "agent2": m["agent2"],
             "answered": list(m["answers"].keys()), "result": m["result"],
             "question": m["question"]["q"],
             "correct_answer": m["question"]["a"] if m["result"] is not None else None}
            for m in matches
        ]
        return jsonify({
            "events": events, "round": tournament["round"], "phase": tournament["phase"],
            "seconds_left": max(0, round(ROUND_TIMEOUT - (time.time() - tournament["started_at"]), 1))
                            if tournament["phase"] == "open" else 0,
            "scores": board, "active_count": len(active_agents()),
            "matches": active_matches, "tiers": TIERS, "tier_emoji": TIER_EMOJI,
        })

@app.route("/next_round", methods=["POST"])
def next_round():
    with lock:
        advance_round(reason="manual")
    return jsonify({"status": "ok", "round": tournament["round"]})

@app.route("/reset", methods=["POST"])
def reset():
    global agents, matches, match_history, log, tournament
    with lock:
        agents = {}; matches = []; match_history = []; log = []
        tournament = {"round": 1, "phase": "waiting", "started_at": time.time()}
        push_log("system", msg="🔄 Tournament reset.")
    return jsonify({"status": "ok", "message": "Tournament reset."})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
