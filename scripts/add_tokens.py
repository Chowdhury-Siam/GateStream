import sys
import os
import re

def main():
    if len(sys.argv) < 2:
        print("❌ Usage: python scripts/add_tokens.py <token1> <token2> ...")
        return

    # Extract all tokens from CLI arguments (support space and comma separated)
    raw_args = " ".join(sys.argv[1:])
    new_tokens = [t.strip() for t in raw_args.replace(",", " ").split() if t.strip()]
    
    if not new_tokens:
        print("❌ No valid tokens provided.")
        return

    target_file = None
    for fn in (".env", "config.env", "start.sh"):
        if os.path.exists(fn):
            target_file = fn
            break

    if not target_file:
        target_file = ".env"
        with open(target_file, "a", encoding="utf-8"):
            pass

    with open(target_file, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = r'(MULTI_BOT_TOKENS\s*=\s*["\'])([^"\']*)(["\'])'
    match = re.search(pattern, content)

    if match:
        prefix, existing, suffix = match.groups()
        existing_tokens = [t.strip() for t in existing.replace(",", " ").split() if t.strip()]
        combined = list(dict.fromkeys(existing_tokens + new_tokens))
        updated_line = f'{prefix}{" ".join(combined)}{suffix}'
        new_content = re.sub(pattern, updated_line, content)
    else:
        pattern_unquoted = r'(MULTI_BOT_TOKENS\s*=\s*)([^\r\n#]*)'
        match_unquoted = re.search(pattern_unquoted, content)
        if match_unquoted:
            prefix, existing = match_unquoted.groups()
            existing_tokens = [t.strip() for t in existing.replace(",", " ").split() if t.strip()]
            combined = list(dict.fromkeys(existing_tokens + new_tokens))
            new_content = re.sub(pattern_unquoted, f'MULTI_BOT_TOKENS="{" ".join(combined)}"', content)
        else:
            combined = list(dict.fromkeys(new_tokens))
            new_content = content.rstrip() + f'\nMULTI_BOT_TOKENS="{" ".join(combined)}"\n'

    with open(target_file, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"✅ Successfully updated {target_file}!")
    print(f"🤖 Total worker bot tokens: {len(combined)}")
    print(f"➕ Added {len(new_tokens)} token(s).")
    print("👉 Now send /restart to boot up all worker bots!")

if __name__ == "__main__":
    main()
