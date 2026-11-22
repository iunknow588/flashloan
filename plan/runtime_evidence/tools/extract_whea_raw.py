import re, json, sys
xml_path, out_path = sys.argv[1], sys.argv[2]
raw = open(xml_path, "rb").read().decode("utf-16", errors="replace")
events = re.findall(r"<Event\s[^>]*>.*?</Event>", raw, re.S)
out = []
for ev in events:
    t = re.search(r"<TimeCreated SystemTime='([^']+)'", ev)
    rid = re.search(r"<EventRecordID>(\d+)</EventRecordID>", ev)
    eid = re.search(r"<EventID>(\d+)</EventID>", ev)
    fields = dict(re.findall(r"<Data Name='([^']+)'>([^<]*)</Data>", ev))
    rdata = fields.get("RawData")
    if not rdata:
        continue
    out.append({
        "time_created": t.group(1) if t else None,
        "event_record_id": int(rid.group(1)) if rid else None,
        "event_id": int(eid.group(1)) if eid else None,
        "whea_id": fields.get("Id"),
        "flags": fields.get("Flags"),
        "length": fields.get("Length"),
        "raw_hex": rdata,
    })
json.dump(out, open(out_path, "w", encoding="utf-8"), indent=1)
print(f"extracted {len(out)} records with RawData -> {out_path}")
