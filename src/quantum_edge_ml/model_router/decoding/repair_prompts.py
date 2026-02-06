SYSTEM_PROMPT = (
    "Return ONLY a single-line JSON object with keys exactly: v,s,c,sl,tp,r,rk. "
    "No extra keys. No prose. Example: {\"v\":1,\"s\":\"HOLD\",\"c\":0.0,"
    "\"sl\":null,\"tp\":null,\"r\":\"ok\",\"rk\":\"LOW\"}"
)

SCHEMA_NOTE = (
    "Schema: v=1 (int), s in BUY|SELL|HOLD|REDUCE|CLOSE, c in [0,1], "
    "sl/tp null or >0, r max 60 chars no newlines, rk in LOW|MED|HIGH|CRIT."
)


def make_user_prompt(user_prompt: str) -> str:
    return user_prompt.strip()


def make_repair_prompt(user_prompt: str, last_output: str, error: str) -> str:
    return (
        f"Previous output invalid: {error}. "
        f"Last output: {last_output}. "
        f"{SCHEMA_NOTE} "
        "Return ONLY the JSON object. "
        f"User prompt: {user_prompt}"
    )
