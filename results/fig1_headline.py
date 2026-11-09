"""Fig. 1: silent-break rate per model under the pre-registered, corrected and audited rules (sample 1, all rungs)."""
import math
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
def wilson(k,n,z=1.959963985):
    p=k/n; d=1+z*z/n; c=(p+z*z/(2*n))/d; h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return p,max(0,c-h),min(1,c+h)
models=["Claude Sonnet 5.5","GPT-6.1 Sol","gpt-oss-120B"]
rules=[("Pre-registered rule",[(15,426),(10,435),(30,396)],"#9aa3ad","o"),
       ("Corrected rule (R6 = SpotBugs only)",[(3,426),(2,435),(15,396)],"#2a78d6","s"),
       ("Audited rule (in-contract breaks)",[(0,426),(0,435),(8,396)],"#eb6834","D")]
fig,ax=plt.subplots(figsize=(3.5,3.1),dpi=200)
offs=[-0.24,0,0.24]
for (name,data,col,mk),off in zip(rules,offs):
    for i,(k,n) in enumerate(data):
        p,lo,hi=wilson(k,n); y=i+off
        ax.plot([lo*100,hi*100],[y,y],color=col,lw=1.4,solid_capstyle="round")
        ax.plot(p*100,y,marker=mk,color=col,ms=5,mec="white",mew=0.6,zorder=3,label=name if i==0 else None)
        ax.text(hi*100+0.4,y,f"{k}/{n}",va="center",fontsize=6.3,color=col)
ax.set_yticks(range(3)); ax.set_yticklabels(models,fontsize=8); ax.invert_yaxis()
ax.set_xlabel("Silent-break rate, % of eligible rewrites",fontsize=7.5)
ax.set_xlim(-0.3,15.0); ax.tick_params(axis="x",labelsize=7)
for s in ("top","right"): ax.spines[s].set_visible(False)
ax.grid(axis="x",color="#e6e6e6",lw=0.6); ax.set_axisbelow(True)
ax.legend(fontsize=6.5,frameon=False,loc="upper center",bbox_to_anchor=(0.42,-0.22),ncol=1,handletextpad=0.4)
fig.tight_layout(); fig.savefig("figures/fig1_headline.pdf"); print("ok")
