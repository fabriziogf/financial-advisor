# `data/` — intentionally empty

Financial data does **not** live here. It is stored outside the repository at:

    ~/.local/share/financial-advisor/

Run `fa init` to create it. Override with `FA_DATA_DIR`.

## Why not here?

Two reasons, either sufficient on its own:

1. **This repo is public.** Anything in the working tree is one `git add -A` away
   from being published permanently.
2. **This repo sits inside an iCloud-synced folder.** `~/Documents` has Desktop &
   Documents sync enabled, so a database written here would upload to Apple's
   servers and sync to every device on the account — silently, on first import.
   FileVault does not help with that; the copy has already left the machine.

Keeping data outside the tree is a structural control rather than a policy one: a
file that isn't here cannot be committed by mistake, and the protection does not
depend on `.gitignore` staying correct forever.

This directory and the `.gitignore` rules covering it are kept as defence in depth.

See [SECURITY.md](../SECURITY.md).
