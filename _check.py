import py_compile
p = r'C:\Coding\scripts\genshin_tweak\EcoGenshinOptimizer.py'
try:
    py_compile.compile(p, doraise=True)
    raise SystemExit('COMPILE_OK')
except py_compile.PyCompileError as e:
    raise SystemExit('COMPILE_ERR_PYC: ' + str(e)[:800])
except Exception as e:
    raise SystemExit('COMPILE_ERR: ' + repr(e)[:800])
