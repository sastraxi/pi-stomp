# NAM Reamp Asset

Place `v3_0_0.wav` in this directory before building the Arch image.

## Obtaining the file

Download the standardized reamp signal from the Neural Amp Modeler trainer:

1. Go to <https://tone3000.com/capture> (or the NAM Colab notebook).
2. Click **"Download input file"** — this gives you `v3_0_0.wav`.
3. Verify: 24-bit PCM, 48 000 Hz, mono, ~3 minutes long.
4. Copy it here as `setup/nam/v3_0_0.wav`.

The file is not committed to this repository to keep the repo lightweight.
The Arch image install copies it to `/opt/pistomp/pi-stomp/setup/nam/v3_0_0.wav`,
which `NamCaptureEngine` uses as its default reamp source.

## Format

| Property    | Value       |
|-------------|-------------|
| Sample rate | 48 000 Hz   |
| Bit depth   | 24-bit PCM  |
| Channels    | Mono (1)    |
| Container   | WAV         |
