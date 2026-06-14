import libtorrent as lt

attrs = [x for x in dir(lt) if "delete" in x.lower() or "remove" in x.lower()]
print("attrs", attrs)
rf = getattr(lt, "remove_flags_t", None)
if rf:
    print("remove_flags_t", [x for x in dir(rf) if not x.startswith("_")])
s = getattr(lt, "session", None)
if s:
    print("session delete", [x for x in dir(s) if "delete" in x.lower()])
