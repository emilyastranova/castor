# README screenshots

Taken from a throwaway demo instance filled with a fictional engagement, never from real
data. Avatars are DiceBear "Lorelei" portraits (CC0).

1. Start the demo API and UI from `.claude/launch.json` (`demo-api` on :8100 with its data
   in `.demo/`, `demo-web` on :5174), or run the same commands by hand.
2. Start an agent for it (host commands; the seeded playbooks invent their output):

   ```bash
   .venv/bin/castor-agent --server ws://localhost:8100/ws/agent --secret demo-agent-secret \
       --name lab-agent-01 --tags lab --allow-host --max-concurrency 6 --workdir /tmp/castor-demo-agent
   ```

   A second, idle agent (`--name kali-02 --runtime podman --no-allow-host` on a machine
   without podman) makes "2 agents online".
3. Seed it: `python3 .github/assets/screenshots/seed_demo.py http://localhost:8100`. It uploads
   the test-fixture playbooks under real-sounding names (Internal recon, SMB signing review).
4. Capture (Playwright in a throwaway venv, not a project dependency):

   ```bash
   python3 -m venv /tmp/pw && /tmp/pw/bin/pip install playwright && /tmp/pw/bin/playwright install chromium
   /tmp/pw/bin/python .github/assets/screenshots/capture.py http://localhost:5174
   ```

   It writes `dashboard`, `board`, `card`, `playbook` (dark), `assets` (light) and `phone`
   (three phone screens joined with ImageMagick's `magick`), in a timezone where it's
   afternoon so Home doesn't say "Working late".

To start over, stop the demo API and delete `.demo/`.
