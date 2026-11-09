"""Audited (in-contract) headline recomputed from the corrected-rule score rows plus the 34-cell audit verdicts."""
import csv, json, sys, collections, itertools, math
import os
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE,'..','harness')); sys.path.insert(0, os.path.join(HERE,'..'))
from paper.stats import wilson, cochran_q, mcnemar_exact, holm
SCORE=os.path.join(HERE,'..','runs','full','score_corrected.jsonl')
# verdicts: cell_id -> resolution
V={}
for r in csv.DictReader(open(os.path.join(HERE,'ooptimize-audit-sheet-resolved.csv'))):
    if r['group']=='A': V[r['cell_id']]=r['resolution']
S2={'c01':'out_of_contract','c02':'out_of_contract','c03':'in_contract_break','c04':'regex_artifact','c05':'out_of_contract',
    'c06':'in_contract_break','c07':'in_contract_break','c08':'regex_artifact','c09':'out_of_contract','c10':'out_of_contract',
    'c11':'unsure','c12':'in_contract_break','c13':'in_contract_break','c14':'in_contract_break'}
S2['c13']='regex_artifact'
br=json.load(open(os.path.join(HERE,'all_breaks.json')))
aud=set(V); i=0
for b in br:
    if b['cell_id'] in aud: continue
    i+=1; V[b['cell_id']]=S2[f'c{i:02d}']
assert len(V)==34, len(V)
last={}
for l in open(SCORE):
    r=json.loads(l); last[r['cell_id']]=r
gen=[r for r in last.values() if r['kind']=='gen' and r.get('status')=='scored']
def cls(r): return r.get('classification') or {}
def audited(r):
    v=V.get(r['cell_id']); return v in ('in_contract_break',)
def unsure(r): return V.get(r['cell_id'])=='unsure'
MODELS=['claude-sonnet-5-5','gpt-6.1-sol','openai/gpt-oss-120b']; RUNGS=['R1','R2','R3']
out={'verdicts':collections.Counter(V.values()),'by_model':{}, 'by_lib':{}, 'ladder':{}, 'stability':{}}
elig=[r for r in gen if cls(r).get('headline_eligible')]
for m in MODELS:
    d={}
    for s in (1,2):
        g=[r for r in elig if r['model']==m and r['sample']==s]
        k=sum(audited(r) for r in g); u=sum(unsure(r) for r in g); c=sum(bool(cls(r).get('silent_break')) for r in g)
        _,lo,hi=wilson(k,len(g)) if g else (0,0,0)
        d[f's{s}']={'n':len(g),'corrected':c,'audited':k,'unsure':u,'rate':k/len(g) if g else 0,'lo':lo,'hi':hi}
        for ru in RUNGS:
            gg=[r for r in g if r['rung']==ru]; kk=sum(audited(r) for r in gg)
            d[f's{s}_{ru}']={'n':len(gg),'audited':kk,'corrected':sum(bool(cls(r).get('silent_break')) for r in gg)}
    out['by_model'][m]=d
for lib in sorted({r['lib'] for r in elig}):
    g=[r for r in elig if r['lib']==lib and r['sample']==1]
    out['by_lib'][lib]={'n':len(g),'corrected':sum(bool(cls(r).get('silent_break')) for r in g),'audited':sum(audited(r) for r in g),
                        'by_model':{m:(sum(audited(r) for r in g if r['model']==m),sum(1 for r in g if r['model']==m)) for m in MODELS}}
# ladder: paired methods with eligible s1 cells in all three rungs
for m in MODELS:
    by=collections.defaultdict(dict)
    for r in elig:
        if r['model']==m and r['sample']==1: by[r['method']][r['rung']]=int(audited(r))
    pairs=[v for v in by.values() if len(v)==3]
    mat=[[v[ru] for ru in RUNGS] for v in pairs]
    q,p,df=cochran_q(mat) if pairs else (0,1,2)
    pm={}
    for a,b in itertools.combinations(RUNGS,2):
        bb=sum(1 for v in pairs if v[a] and not v[b]); cc=sum(1 for v in pairs if v[b] and not v[a])
        pm[f'{a}v{b}']=(bb,cc,mcnemar_exact(bb,cc))
    out['ladder'][m]={'paired':len(pairs),'rates':[sum(v[ru] for v in pairs)/len(pairs) if pairs else 0 for ru in RUNGS],'Q':q,'p':p,'mcnemar':pm,'holm':holm({k:v[2] for k,v in pm.items()})}
# stability: methods with eligible cells in both samples, same model+rung
for m in MODELS:
    flips=0; n=0
    for ru in RUNGS:
        by=collections.defaultdict(dict)
        for r in elig:
            if r['model']==m and r['rung']==ru: by[r['method']][r['sample']]=int(audited(r))
        pr=[v for v in by.values() if len(v)==2]; n+=len(pr); flips+=sum(1 for v in pr if v[1]!=v[2])
    out['stability'][m]={'pairs':n,'flips':flips}
# Fisher oss vs hosted (s1): audited
def fisher(a,b,c,d):
    from math import comb
    n=a+b+c+d; r1=a+b; c1=a+c
    p0=comb(r1,a)*comb(n-r1,c1-a)/comb(n,c1); p=0.0
    for x in range(max(0,c1-(n-r1)),min(r1,c1)+1):
        px=comb(r1,x)*comb(n-r1,c1-x)/comb(n,c1)
        if px<=p0*(1+1e-9): p+=px
    return p
oss=out['by_model']['openai/gpt-oss-120b']['s1']; h1=out['by_model']['claude-sonnet-5-5']['s1']; h2=out['by_model']['gpt-6.1-sol']['s1']
out['fisher_oss_vs_hosted_s1']=fisher(oss['audited'],oss['n']-oss['audited'],h1['audited']+h2['audited'],h1['n']+h2['n']-h1['audited']-h2['audited'])
# methods carrying audited breaks
meth=collections.Counter(r['method'] for r in elig if audited(r)); out['methods']=dict(meth)
out['verdicts_by_model']={m:dict(collections.Counter(V[c] for c in V if last[c]['model']==m)) for m in MODELS}
json.dump(out,open(os.path.join(HERE,'audited_numbers.json'),'w'),indent=1,default=str)
print(json.dumps(out,indent=1,default=str))
