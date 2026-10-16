import json, sys
canary = False; reads = []; res = ''
for l in open(sys.argv[1]):
    d = json.loads(l)
    if d.get('type') == 'assistant':
        for b in d['message']['content']:
            if b.get('type') == 'tool_use' and 'skill-overlays' in json.dumps(b['input']):
                reads.append(b['name'])
    if d.get('type') == 'result':
        res = d.get('result') or ''
print(f"{sys.argv[1]}: canary_in_answer={'AC7-CANARY-90210' in res} overlay_reads={reads}")
