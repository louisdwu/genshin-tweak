import importlib.util as u
for m in ('PIL','psutil','matplotlib','pystray'):
    try:
        spec = u.find_spec(m)
        print(m, ('OK' if spec else 'NO'))
    except Exception as e:
        print(m, 'ERR', repr(e)[:60])
