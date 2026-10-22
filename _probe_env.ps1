$ErrorActionPreference = "SilentlyContinue"
$out = @()

# 注册表中的 Python 安装路径 (HKLM 和 HKCU, 64/32 位)
$regRoots = @(
    "HKLM:\SOFTWARE\Python\PythonCore",
    "HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore",
    "HKCU:\SOFTWARE\Python\PythonCore"
)
foreach ($root in $regRoots) {
    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
        $ver = $_.PSChildName
        $ip = (Get-ItemProperty "$($_.PSPath)\InstallPath" -ErrorAction SilentlyContinue)
        if ($ip) {
            $d = $ip.'(default)'
            if (-not $d) { $d = $ip.PSObject.Properties.Name | Where-Object { $_ -ne 'PSPath' -and $_ -ne 'PSParentPath' -and $_ -ne 'PSChildName' -and $_ -ne 'PSDrive' -and $_ -ne 'PSProvider' } | ForEach-Object { $ip.$_ } | Select-Object -First 1 }
            $out += "REG  $ver  =>  $d"
        }
    }
}

# 常见目录扫描 python.exe / pythonw.exe
$dirs = @(
    "C:\Python314","C:\Python313","C:\Python312","C:\Python311","C:\Python310",
    "C:\Python39","C:\Python38",
    "C:\Users\Louis\AppData\Local\Programs\Python",
    "C:\Program Files\Python314","C:\Program Files\Python313","C:\Program Files\Python312","C:\Program Files\Python311","C:\Program Files\Python310",
    "C:\ProgramData\Anaconda3","C:\Users\Louis\anaconda3","C:\Users\Louis\miniconda3",
    "C:\Users\Louis\AppData\Local\Programs\Python\Python314",
    "C:\Users\Louis\AppData\Local\Programs\Python\Python313",
    "C:\Users\Louis\AppData\Local\Programs\Python\Python312",
    "C:\Users\Louis\AppData\Local\Programs\Python\Python311",
    "C:\Users\Louis\AppData\Local\Programs\Python\Python310"
)
foreach ($d in $dirs) {
    Get-ChildItem -Path $d -Filter "python.exe" -Recurse -Depth 1 -ErrorAction SilentlyContinue | ForEach-Object {
        $out += "FILE python.exe     =>  $($_.FullName)"
    }
    Get-ChildItem -Path $d -Filter "pythonw.exe" -Recurse -Depth 1 -ErrorAction SilentlyContinue | ForEach-Object {
        $out += "FILE pythonw.exe    =>  $($_.FullName)"
    }
}

$out | Out-File -FilePath "C:\Coding\scripts\genshin_tweak\_probe_out.txt" -Encoding UTF8
