# DecisionV1 Contract (Compact)

## Fields

- `v` (int, required): must be `1`
- `s` (string enum, required): `BUY` | `SELL` | `HOLD` | `REDUCE` | `CLOSE`
- `c` (float, required): confidence in `[0.0, 1.0]`
- `sl` (number or null, optional): stop loss, must be `> 0` if present
- `tp` (number or null, optional): take profit, must be `> 0` if present
- `r` (string, required): reason, max 60 chars, no newlines
- `rk` (string enum, required): `LOW` | `MED` | `HIGH` | `CRIT`

## Rules

- Strict mode: unknown keys are rejected.
- Missing required keys are rejected.
- Output must be a single-line JSON object.

## JSON Schema (human-readable)

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["v", "s", "c", "r", "rk"],
  "properties": {
    "v": {"type": "integer", "enum": [1]},
    "s": {"type": "string", "enum": ["BUY", "SELL", "HOLD", "REDUCE", "CLOSE"]},
    "c": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    "sl": {"type": ["number", "null"], "exclusiveMinimum": 0.0},
    "tp": {"type": ["number", "null"], "exclusiveMinimum": 0.0},
    "r": {"type": "string", "maxLength": 60, "pattern": "^[^\\n\\r]*$"},
    "rk": {"type": "string", "enum": ["LOW", "MED", "HIGH", "CRIT"]}
  }
}
```

## Examples

Valid:

```json
{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"range bound","rk":"LOW"}
```

Invalid (extra key):

```json
{"v":1,"s":"HOLD","c":0.2,"sl":null,"tp":null,"r":"ok","rk":"LOW","x":1}
```

## Versioning

- The `v` field is required and must be `1` for DecisionV1.
- Breaking changes must bump `v` and add a new contract file.
