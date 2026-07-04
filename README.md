# MtpManager
I want to use an old MP3 player but MTP is a lovecraftian horror. This work-in-progress helps me load *most* of my music. Random tracks won't transfer for reasons unknown.

## Running

```bash
./MtpManager.sh
```

## Platform setup

macOS and Linux have different Python/Tkinter/libmtp requirements. See **[PLATFORMS.md](PLATFORMS.md)** before setting up on a new machine — especially on **macOS 26+**, where the system Python's Tkinter will crash and pymtp cannot find libmtp without the project wrapper.
