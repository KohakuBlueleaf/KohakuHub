import sys
import json
from pathlib import Path

# Add src to path so we can import kohakuhub
src_path = Path(__file__).parent.parent / "src"
sys.path.append(str(src_path))

from kohakuhub.config import Config

schema =  Config.model_json_schema()

# Write to file
output_dir = Path(__file__).parent.parent / "__generated__" / "schemas"
output_dir.mkdir(parents=True, exist_ok=True)
output_file = output_dir / "config.json"

def move_defs_to_end(schema: dict) -> dict:
    """Move $defs to the end of the schema."""
    if '$defs' in schema:
        defs = schema.pop('$defs')
        schema['$defs'] = defs
    return schema

schema = move_defs_to_end(schema)

with open(output_file, "w") as f:
    json.dump(schema, f, indent=2)

print(f"Schema written to {output_file}")
