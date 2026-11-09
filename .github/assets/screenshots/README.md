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

## Showcase GIFs

`showcase.py` records the README's GIFs (`.github/assets/showcase/`) from the same demo
instance, with ffmpeg installed. The first scene runs a plain `nmap` command, so start the
agent with `demo-bin/` first on its `PATH`: its `nmap` is a stand-in that prints a believable
service scan and writes matching XML without sending a packet.

```bash
PATH="$PWD/.github/assets/screenshots/demo-bin:$PATH" .venv/bin/castor-agent \
    --server ws://localhost:8100/ws/agent --secret demo-agent-secret --name lab-agent-01 --tags lab \
    --allow-host --max-concurrency 6 --workdir /tmp/castor-demo-agent
/tmp/pw/bin/python .github/assets/screenshots/showcase.py http://localhost:5174            # all four
/tmp/pw/bin/python .github/assets/screenshots/showcase.py http://localhost:5174 run-a-card # one
```

Each scene sets up the cards and runs it needs through the API (and rewrites the seeded
playbooks so their run dialogs ask only for a subnet), so scenes can be re-recorded on the same
instance. Frames come from Chromium's screencast at 2x and are encoded at 12 fps with a
128-colour palette per GIF. `KEEP_FRAMES=1` keeps the frames to try other encoder settings.
