"""Method-level inference for the audited headline: paired permutation test (model labels shuffled within method)
and method-clustered bootstrap. Reads the corrected score rows and the resolved verdict sheet."""
import json, csv, random, collections, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
V={}
for r in csv.DictReader(open('ooptimize-audit-sheet-resolved.csv')):
    if r['group']=='A': V[r['cell_id']]=r['resolution']
S2=['out_of_contract','out_of_contract','in_contract_break','regex_artifact','out_of_contract','in_contract_break','in_contract_break',
    'regex_artifact','out_of_contract','out_of_contract','unsure','in_contract_break','regex_artifact','in_contract_break']
br=json.load(open('all_breaks.json')); i=0
for b in br:
    if b['cell_id'] in V: continue
    V[b['cell_id']]=S2[i]; i+=1
last={}
for l in open('../runs/full/score_corrected.jsonl'):
    r=json.loads(l); last[r['cell_id']]=r
M=['claude-sonnet-5-5','gpt-6.1-sol','openai/gpt-oss-120b']
elig=[r for r in last.values() if r['kind']=='gen' and r.get('status')=='scored'
      and (r.get('classification') or {}).get('headline_eligible') and r['sample']==1]
cells=collections.defaultdict(lambda:{m:[] for m in M})
for r in elig: cells[r['method']][r['model']].append(int(V.get(r['cell_id'])=='in_contract_break'))
meths=[m for m in cells if all(cells[m][x] for x in M)]
brk={m:{x:int(any(cells[m][x])) for x in M} for m in meths}
print('methods with eligible cells for all models:',len(meths),'method-level breaks:',{x:sum(brk[m][x] for m in meths) for x in M})
stat=lambda b: sum(b[m][M[2]] for m in meths)-0.5*sum(b[m][M[0]]+b[m][M[1]] for m in meths)
obs=stat(brk); random.seed(1); N=200000; cnt=0
for _ in range(N):
    bb={}
    for m in meths:
        v=[brk[m][x] for x in M]; random.shuffle(v); bb[m]=dict(zip(M,v))
    cnt+=stat(bb)>=obs-1e-9
print('paired permutation p =',(cnt+1)/(N+1))
ms=list(cells); random.seed(2); rates=[]
for _ in range(10000):
    smp=[random.choice(ms) for _ in ms]; k=n=0
    for s in smp: k+=sum(cells[s][M[2]]); n+=len(cells[s][M[2]])
    rates.append(k/n)
rates.sort(); print('gpt-oss rate, method-clustered bootstrap 95%:',rates[250],rates[9750])
