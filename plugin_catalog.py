"""Where to get the plugins Freighter can't bring (issue #82): the major brands, how each one installs its plugins,
and their popular plugins with a type for the page's chips. Data only.

Links were checked on 2026-11-22. A plugin's own link is a search on its maker's site, which keeps working when
product pages move; brands without a working site search link to their products page instead.
"""
import re

# kinds of plugin, for the chips on the page
TYPES = ("Synth", "Sampler", "Drums", "Bass", "Keys", "Orchestral", "Guitar", "Vocal", "Mixing", "Mastering",
         "Reverb", "Delay", "Effect")

ILOK_TIP = "Uses iLok: keep iLok License Manager installed, and move the license there if it's tied to the old computer."

BRANDS = [
    {"id": "native-instruments", "name": "Native Instruments", "match": ["native instruments"], "color": "#e8a33d",
     "app": "Native Access", "url": "https://www.native-instruments.com/pages/native-access",
     "search": "https://www.native-instruments.com/search?q={q}",
     "tip": "Brought your Kontakt libraries? In Native Access choose Locate instead of downloading them again.",
     "plugins": {"Massive": "Synth", "Massive X": "Synth", "Kontakt": "Sampler", "Battery": "Drums", "FM8": "Synth",
                 "Reaktor": "Synth", "Guitar Rig": "Guitar", "Raum": "Reverb", "Replika": "Delay", "Monark": "Synth",
                 "Una Corda": "Keys", "The Gentleman": "Keys", "Absynth": "Synth"}},
    {"id": "izotope", "name": "iZotope", "match": ["izotope"], "color": "#6c8cff",
     "app": "Native Access", "url": "https://www.native-instruments.com/pages/native-access",
     "search": "https://www.izotope.com/search?q={q}",
     "tip": "iZotope plugins now install through Native Access, with your iZotope account.",
     "plugins": {"Ozone": "Mastering", "Neutron": "Mixing", "Nectar": "Vocal", "RX": "Mixing", "Trash": "Effect",
                 "VocalSynth": "Vocal", "Neoverb": "Reverb", "Stratus": "Reverb", "Vinyl": "Effect"}},
    {"id": "waves", "name": "Waves", "match": ["waves audio", "waves"], "color": "#3ec1d3",
     "app": "Waves Central", "url": "https://www.waves.com/downloads/central",
     "search": "https://www.waves.com/search?q={q}",
     "tip": "Your licenses are in your Waves account: activate them on this computer in Waves Central.",
     "plugins": {"CLA-76": "Mixing", "CLA-2A": "Mixing", "SSL G-Master Buss Compressor": "Mixing", "H-Delay": "Delay",
                 "H-Reverb": "Reverb", "Renaissance Vox": "Vocal", "L2 Ultramaximizer": "Mastering",
                 "Waves Tune": "Vocal", "OVox": "Vocal", "Abbey Road Chambers": "Reverb", "RVox": "Vocal",
                 "SSL E-Channel": "Mixing", "PuigTec EQP-1A": "Mixing"}},
    {"id": "arturia", "name": "Arturia", "match": ["arturia"], "color": "#ff7a59",
     "app": "Arturia Software Center", "url": "https://www.arturia.com/support/downloads-manuals",
     "products": "https://www.arturia.com/products",
     "plugins": {"Pigments": "Synth", "Analog Lab": "Synth", "Mini V": "Synth", "Jup-8 V": "Synth",
                 "Prophet-5 V": "Synth", "CS-80 V": "Synth", "Piano V": "Keys", "Stage-73 V": "Keys",
                 "Comp FET-76": "Mixing", "Rev PLATE-140": "Reverb"}},
    {"id": "plugin-alliance", "name": "Plugin Alliance", "match": ["plugin alliance", "brainworx"], "color": "#f2c94c",
     "app": "Plugin Alliance Installation Manager", "url": "https://www.plugin-alliance.com/pages/installation-manager",
     "search": "https://www.plugin-alliance.com/search?q={q}",
     "plugins": {"bx_digital": "Mastering", "bx_console": "Mixing", "Shadow Hills Mastering Compressor": "Mastering",
                 "Lindell 80": "Mixing", "SPL Transient Designer": "Mixing", "Black Box Analog Design HG-2": "Mastering",
                 "Maag EQ4": "Mixing", "bx_masterdesk": "Mastering"}},
    {"id": "spitfire", "name": "Spitfire Audio", "match": ["spitfire audio", "spitfire"], "color": "#c9a46b",
     "app": "Spitfire Audio app", "url": "https://support.spitfireaudio.com/en/articles/11815288-the-spitfire-audio-app",
     "search": "https://www.spitfireaudio.com/search?q={q}",
     "tip": "Brought the library folders? In the Spitfire app choose Locate instead of downloading them again.",
     "plugins": {"LABS": "Sampler", "BBC Symphony Orchestra": "Orchestral", "Albion": "Orchestral",
                 "Spitfire Audio": "Orchestral", "Originals": "Orchestral", "Abbey Road": "Orchestral"}},
    {"id": "uvi", "name": "UVI", "match": ["uvi"], "color": "#8fd16a",
     "app": "UVI Portal", "url": "https://www.uvi.net/uvi-portal", "search": "https://www.uvi.net/search?q={q}",
     "plugins": {"Falcon": "Synth", "UVI Workstation": "Sampler", "Shade": "Effect", "Thorus": "Effect"}},
    {"id": "softube", "name": "Softube", "match": ["softube"], "color": "#ef6f6c",
     "app": "Softube Central", "url": "https://www.softube.com/support", "search": "https://www.softube.com/search?q={q}",
     "tip": ILOK_TIP,
     "plugins": {"Console 1": "Mixing", "Tape": "Mixing", "Weiss DS1": "Mastering", "Harmonics": "Effect",
                 "Model 84": "Synth", "Model 72": "Synth", "Tube-Tech": "Mixing"}},
    {"id": "slate", "name": "Slate Digital", "match": ["slate digital", "slate"], "color": "#9aa4b2",
     "app": "Complete Access Hub",
     "url": "https://support.slatedigital.com/hc/en-us/articles/43819908914451-Complete-Access-Hub-Getting-Started-Legacy-App-Migration",
     "search": "https://slatedigital.com/?s={q}",
     "tip": "Slate Digital Connect has been replaced by Complete Access Hub. " + ILOK_TIP,
     "plugins": {"Virtual Mix Rack": "Mixing", "VMR": "Mixing", "Fresh Air": "Mixing", "FG-X": "Mastering",
                 "Virtual Tape Machines": "Mixing", "VerbSuite Classics": "Reverb", "Infinity EQ": "Mixing"}},
    {"id": "universal-audio", "name": "Universal Audio", "match": ["universal audio", "uaudio"], "color": "#d9d9d9",
     "app": "UA Connect", "url": "https://www.uaudio.com/pages/download-ua-connect",
     "search": "https://www.uaudio.com/search?q={q}",
     "plugins": {"1176": "Mixing", "LA-2A": "Mixing", "Pultec": "Mixing", "Ampex ATR-102": "Mixing",
                 "Lexicon 224": "Reverb", "Galaxy Tape Echo": "Delay", "Neve 1073": "Mixing", "Studer A800": "Mixing"}},
    {"id": "output", "name": "Output", "match": ["output"], "color": "#ff5fa2",
     "app": "Output Hub", "url": "https://output.com/hub", "products": "https://output.com/products",
     "tip": "Arcade installs from your Output account (the Arcade tab), not from Output Hub.",
     "plugins": {"Arcade": "Sampler", "Portal": "Effect", "Movement": "Effect", "Thermal": "Effect",
                 "Substance": "Bass", "Exhale": "Vocal", "Analog Strings": "Orchestral", "Signal": "Synth"}},
    {"id": "toontrack", "name": "Toontrack", "match": ["toontrack"], "color": "#f08a24",
     "app": "Toontrack Product Manager", "url": "https://www.toontrack.com/product-manager/",
     "search": "https://www.toontrack.com/?s={q}",
     "plugins": {"EZdrummer": "Drums", "Superior Drummer": "Drums", "EZkeys": "Keys", "EZbass": "Bass",
                 "EZmix": "Mixing"}},
    {"id": "eastwest", "name": "EastWest", "match": ["eastwest", "east west"], "color": "#b07cff",
     "app": "EW Installation Center", "url": "https://www.soundsonline.com/support/updates",
     "search": "https://www.soundsonline.com/search?q={q}",
     "plugins": {"Opus": "Orchestral", "Play": "Sampler", "Hollywood Orchestra": "Orchestral",
                 "Symphonic Choirs": "Orchestral", "Spaces": "Reverb"}},
    {"id": "ik", "name": "IK Multimedia", "match": ["ik multimedia"], "color": "#4cc38a",
     "app": "IK Product Manager", "url": "https://www.ikmultimedia.com/products/productmanager/",
     "search": "https://www.ikmultimedia.com/search?q={q}",
     "plugins": {"AmpliTube": "Guitar", "T-RackS": "Mastering", "SampleTank": "Sampler", "MODO BASS": "Bass",
                 "MODO DRUM": "Drums", "TONEX": "Guitar", "Syntronik": "Synth"}},
    {"id": "kilohearts", "name": "Kilohearts", "match": ["kilohearts"], "color": "#5ad1ff",
     "app": "Kilohearts Installer", "url": "https://kilohearts.com/download/", "products": "https://kilohearts.com/download/",
     "plugins": {"Phase Plant": "Synth", "Multipass": "Effect", "Snap Heap": "Effect", "Disperser": "Effect",
                 "Kilohearts Essentials": "Effect", "Faturator": "Effect"}},
    {"id": "steinberg", "name": "Steinberg", "match": ["steinberg"], "color": "#e05252",
     "app": "Steinberg Download Assistant", "url": "https://www.steinberg.net/sda",
     "products": "https://www.steinberg.net/vst-instruments/",
     "plugins": {"HALion": "Sampler", "Groove Agent": "Drums", "Retrologue": "Synth", "Padshop": "Synth",
                 "The Grand": "Keys", "Backbone": "Drums"}},
    {"id": "roland", "name": "Roland", "match": ["roland"], "color": "#ff4d4d",
     "app": "Roland Cloud Manager", "url": "https://www.roland.com/cloud/",
     "search": "https://www.roland.com/global/search/?q={q}",
     "plugins": {"ZENOLOGY": "Synth", "TR-808": "Drums", "TR-909": "Drums", "JUNO-106": "Synth",
                 "JUPITER-8": "Synth", "SH-101": "Synth", "D-50": "Synth", "JUNO-60": "Synth"}},
    {"id": "image-line", "name": "Image-Line", "match": ["image-line", "image line"], "color": "#ff9f1c",
     "app": "the FL Studio installer", "url": "https://www.image-line.com/fl-studio/download",
     "products": "https://www.image-line.com/fl-studio/plugins/",
     "steps": ["Download FL Studio", "Sign in with your Image-Line account", "Your FL plugins unlock with it"],
     "tip": "FL Studio's own plugins come with FL Studio: sign in to unlock the ones you bought.",
     "plugins": {"Gross Beat": "Effect", "Harmor": "Synth", "Sytrus": "Synth", "FLEX": "Synth", "Vocodex": "Vocal",
                 "Pitcher": "Vocal", "Transistor Bass": "Bass", "Edison": "Effect", "FPC": "Drums",
                 "Slicex": "Sampler", "DirectWave": "Sampler", "FL Studio": "Effect"}},
    {"id": "spectrasonics", "name": "Spectrasonics", "match": ["spectrasonics"], "color": "#7f8cff",
     "app": "your Spectrasonics account",
     "url": "https://support.spectrasonics.net/manual/Omnisphere2/25/en/topic/re-installing-omnisphere",
     "products": "https://www.spectrasonics.net/products/omnisphere/",
     "steps": ["Sign in to your Spectrasonics account", "Reinstall tab: download the installer",
               "Install, then authorize this computer"],
     "tip": "Brought the STEAM folder? Point Omnisphere at it when asked, instead of downloading the library again.",
     "plugins": {"Omnisphere": "Synth", "Keyscape": "Keys", "Trilian": "Bass", "Stylus RMX": "Drums"}},
    {"id": "refx", "name": "reFX", "match": ["refx"], "color": "#2fd3a2",
     "app": "reFX Cloud", "url": "https://refx.com/cloud/manual/", "products": "https://refx.com/",
     "plugins": {"Nexus": "Synth", "Vanguard": "Synth", "Beast": "Synth"}},
    {"id": "soundtoys", "name": "Soundtoys", "match": ["soundtoys", "sound toys"], "color": "#ffb347",
     "app": "your Soundtoys account",
     "url": "https://support.soundtoys.com/category/10-installation-and-compatibility",
     "search": "https://www.soundtoys.com/?s={q}",
     "steps": ["Sign in to your Soundtoys account", "My Products: download the installer", "Install, then activate"],
     "tip": ILOK_TIP,
     "plugins": {"Decapitator": "Effect", "EchoBoy": "Delay", "Little AlterBoy": "Vocal", "Crystallizer": "Effect",
                 "Radiator": "Effect", "PrimalTap": "Delay", "SuperPlate": "Reverb", "Little Plate": "Reverb",
                 "Devil-Loc": "Effect", "MicroShift": "Effect", "Soundtoys": "Effect"}},
    {"id": "antares", "name": "Antares", "match": ["antares"], "color": "#ff6ad5",
     "app": "Auto-Tune Central",
     "url": "https://help.antarestech.com/hc/en-us/articles/41106855798676-Where-can-I-find-Antares-plug-in-installers",
     "products": "https://www.antarestech.com/products",
     "plugins": {"Auto-Tune Pro": "Vocal", "Auto-Tune Access": "Vocal", "Auto-Tune EFX": "Vocal",
                 "Auto-Tune Artist": "Vocal", "Auto-Tune": "Vocal", "Harmony Engine": "Vocal", "Mic Mod": "Vocal"}},
    {"id": "eventide", "name": "Eventide", "match": ["eventide"], "color": "#5fa8ff",
     "app": "your Eventide account", "url": "https://www.eventideaudio.com/support/installing-and-authorizing-plug-ins/",
     "search": "https://www.eventideaudio.com/?s={q}",
     "steps": ["Sign in to your Eventide account", "Download the plugin installers", "Install, then authorize"],
     "tip": ILOK_TIP,
     "plugins": {"Blackhole": "Reverb", "H3000": "Effect", "UltraTap": "Delay", "MangledVerb": "Reverb",
                 "Physion": "Effect", "H910": "Effect", "SP2016": "Reverb", "Instant Phaser": "Effect"}},
    {"id": "celemony", "name": "Celemony", "match": ["celemony"], "color": "#ffd166",
     "app": "your Celemony account", "url": "https://www.celemony.com/en/service1",
     "products": "https://www.celemony.com/en/melodyne/what-is-melodyne",
     "steps": ["Sign in to your Celemony account", "Download Melodyne", "Install, then activate"],
     "plugins": {"Melodyne": "Vocal"}},
]

DEFAULT_STEPS = ["Get {app}", "Sign in", "Install these"]

# a type from the name when the catalog doesn't know the plugin (first match wins)
TYPE_WORDS = [
    ("Reverb", r"verb|plate|hall|chamber|room|space"),
    ("Delay", r"delay|echo|tap"),
    ("Vocal", r"tune|vocal|vox|voice|harmon|pitch|melod"),
    ("Mastering", r"master|maximi|limiter"),
    ("Drums", r"drum|808|909|kick|snare|beat|perc"),
    ("Keys", r"piano|keys|rhodes|organ|wurl|grand"),
    ("Guitar", r"guitar|amp\b|amplitube|cab\b"),
    ("Bass", r"bass"),
    ("Orchestral", r"string|orchestr|choir|brass|symphon"),
    ("Mixing", r"comp|eq\b|equal|channel|console|tape|bus|clip|sat"),
    ("Synth", r"synth|osc|wave"),
    ("Sampler", r"sampl"),
]


def _plain(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _matches(word, vendor):
    """A short maker name ("Waves", "UVI", "Slate") must be the vendor's first word, so Wavesfactory isn't Waves;
    a longer one may sit anywhere ("Native-instruments", from a Mac bundle ID, is Native Instruments)."""
    w, v = _plain(word), _plain(vendor)
    if len(w) <= 6:
        first = _plain((vendor or "").replace("-", " ").split()[0]) if (vendor or "").split() else ""
        return first == w
    return w in v


def brand_for(vendor):
    """The catalog brand a plugin's maker belongs to, or None (the longest matching name wins)."""
    if not _plain(vendor):
        return None
    hits = [(len(_plain(m)), b) for b in BRANDS for m in b["match"] if _matches(m, vendor)]
    return max(hits, key=lambda h: h[0])[1] if hits else None


def plugin_type(name, brand=None):
    """A plugin's kind for its chip: from the brand's list (the longest name it contains), else from words in
    its name. None when there's no good guess."""
    n = _plain(name)
    if brand:
        known = [(len(_plain(k)), t) for k, t in brand["plugins"].items() if _plain(k) and _plain(k) in n]
        if known:
            return max(known)[1]
    low = (name or "").lower()
    for kind, pattern in TYPE_WORDS:
        if re.search(pattern, low):
            return kind
    return None


def plugin_link(name, brand):
    """Where to read about one plugin: a search on its maker's site, else the brand's products page."""
    from urllib.parse import quote_plus
    if brand.get("search"):
        return brand["search"].replace("{q}", quote_plus(name))
    return brand.get("products") or brand["url"]


def brand_steps(brand):
    return brand.get("steps") or [s.replace("{app}", brand["app"]) for s in DEFAULT_STEPS]


def initials(name):
    """Two letters for the brand's tile: the first letters of two words, else the first two letters."""
    words = [w for w in re.split(r"[\s\-]+", name) if w]
    if words[0].isupper() and len(words[0]) <= 3:   # UVI, IK Multimedia
        return words[0]
    if len(words) > 1:
        return (words[0][0] + words[1][0]).upper()
    return name[:2] if name.isupper() else name[0].upper() + name[1].lower()
