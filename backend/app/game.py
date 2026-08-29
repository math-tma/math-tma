import random


def _generate_one(difficulty_level: int) -> tuple[str, int]:
    """Returns (question_text, correct_answer). Difficulty grows slightly with level."""
    ops = ["+", "-", "\u00d7"]
    op = random.choice(ops)
    max_n = min(12 + difficulty_level, 50)

    if op == "+":
        a, b = random.randint(1, max_n), random.randint(1, max_n)
        answer = a + b
    elif op == "-":
        a, b = random.randint(1, max_n), random.randint(1, max_n)
        if b > a:
            a, b = b, a
        answer = a - b
    else:  # multiplication kept small so mental math stays fast
        a, b = random.randint(2, 9 + min(difficulty_level, 4)), random.randint(2, 9)
        answer = a * b

    return f"{a} {op} {b} = ?", answer


def _distractors(correct: int) -> list[int]:
    """Three wrong-but-plausible answers, close to the correct one."""
    options = {correct}
    spread = max(2, abs(correct) // 5)
    while len(options) < 4:
        delta = random.randint(-spread, spread)
        if delta == 0:
            continue
        candidate = correct + delta
        if candidate != correct:
            options.add(candidate)
    return list(options)


def generate_batch(count: int = 40) -> list[dict]:
    """
    Generates more problems than a player could realistically answer in
    GAME_DURATION_SECONDS, so the client never runs out mid-round.
    """
    batch = []
    for i in range(count):
        question, correct = _generate_one(difficulty_level=i // 8)
        options = _distractors(correct)
        random.shuffle(options)
        correct_index = options.index(correct)
        batch.append({
            "question": question,
            "options": [str(o) for o in options],
            "correct_index": correct_index,
        })
    return batch
