import winreg, os, json, sys

roots = {
    "HKLM": (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Python\PythonCore"),
    "HKLM_WOW": (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Python\PythonCore"),
    "HKCU": (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Python\PythonCore"),
}
found = {}
for tag, (hive, path) in roots.items():
    try:
        k = winreg.OpenKey(hive, path)
        i = 0
        while True:
            try:
                ver = winreg.EnumKey(k, i); i += 1
            except OSError:
                break
            try:
                ik = winreg.OpenKey(k, ver + r"\InstallPath")
                val, _ = winreg.QueryValueEx(ik, "")
                found.setdefault(tag, {})[ver] = val
                winreg.CloseKey(ik)
            except OSError:
                pass
        winreg.CloseKey(k)
    except OSError:
        pass

# scan common dirs
cands = []
dirs = [
    r"C:\Python314", r"C:\Python313", r"C:\Python312", r"C:\Python311", r"C:\Python310",
    r"C:\Python39", r"C:\Python38",
    r"C:\Users\Louis\AppData\Local\Programs\Python",
    r"C:\Program Files\Python314", r"C:\Program Files\Python313", r"C:\Program Files\Python312",
    r"C:\Program Files\Python311", r"C:\Program Files\Python310",
    r"C:\ProgramData\Anaconda3", r"C:\Users\Louis\anaconda3", r"C:\Users\Louis\miniconda3",
]
for d in dirs:
    if not os.path.isdir(d):
        continue
    for sub in ("", "Python314", "Python313", "Python312", "Python311", "Python310"):
        p = os.path.join(d, sub) if sub else d
        for name in ("python.exe", "pythonw.exe"):
            fp = os.path.join(p, name)
            if os.path.isfile(fp):
                cands.append(fp)
    # recurse one level
    try:
        for sub in os.listdir(d):
            sp = os.path.join(d, sub)
            if os.path.isdir(sp):
                for name in ("python.exe", "pythonw.exe"):
                    fp = os.path.join(sp, name)
                    if os.path.isfile(fp):
                        cands.append(fp)
    except OSError:
        pass

out = {"reg": found, "files": sorted(set(cands))}
with open(r"C:\Coding\scripts\genshin_tweak\_scan_out.txt", "w", encoding="ascii") as f:
    json.dump(out, f, indent=1)
