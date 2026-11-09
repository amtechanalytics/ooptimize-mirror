import java.util.*;
import java.util.function.*;
public class Driver {
  static String run(Supplier<Object> s){ try { Object o=s.get(); return (o instanceof char[])? Arrays.toString((char[])o) : (o instanceof byte[])? Arrays.toString((byte[])o) : String.valueOf(o);} catch(Throwable t){ return "EXC:"+t.getClass().getSimpleName(); } }
  static Random R=new Random(7);
  static char[] sortedChars(int n,int max){ TreeSet<Character> s=new TreeSet<>(); while(s.size()<n) s.add((char)R.nextInt(max)); char[] a=new char[n]; int i=0; for(char c:s)a[i++]=c; return a; }
  static void report(String name, Map<String,Integer> valid, Map<String,Integer> mal, Map<String,String> wit){
    System.out.println("### "+name+"  valid-input diffs="+valid.getOrDefault("diff",0)+"/"+valid.get("n")+"  malformed-input diffs="+mal.getOrDefault("diff",0)+"/"+mal.get("n"));
    for(String k: wit.keySet()) System.out.println("   witness["+k+"] "+wit.get(k));
  }
  interface Case { String orig(); String pat(); }
  static void fuzz(String name, int trials, Supplier<Object[]> gen, Function<Object[],Boolean> isValid, Function<Object[],String> orig, Function<Object[],String> pat, Function<Object[],String> show){
    Map<String,Integer> v=new HashMap<>(), m=new HashMap<>(); v.put("n",0); m.put("n",0); Map<String,String> wit=new LinkedHashMap<>();
    for(int t=0;t<trials;t++){ Object[] in=gen.get(); boolean valid=isValid.apply(in); Map<String,Integer> tgt=valid?v:m; tgt.merge("n",1,Integer::sum);
      String a=orig.apply(in), b=pat.apply(in); if(!a.equals(b)){ tgt.merge("diff",1,Integer::sum); String key=(valid?"valid":"malformed")+" "+(a.startsWith("EXC:")?a:"VALUE")+" -> "+(b.startsWith("EXC:")?b:"VALUE"); if(!wit.containsKey(key)) wit.put(key, show.apply(in)+"  orig="+a+"  patched="+b);} }
    report(name,v,m,wit);
  }
  public static void main(String[] x){
    // --- sortSmallByteArray rows 1,7,15
    for(int row: new int[]{1,7,15}){
      final int rr=row;
      fuzz("row"+row+" sortSmallByteArray", 300000, ()->{ int n=R.nextInt(8); byte[] key=new byte[n]; for(int i=0;i<n;i++) key[i]=(byte)R.nextInt(256);
          Object[] ch = R.nextInt(10)==0? null : new Object[R.nextInt(9)]; if(ch!=null) for(int i=0;i<ch.length;i++) ch[i]=Integer.valueOf(i);
          int left=R.nextInt(12)-3, right=R.nextInt(12)-3; return new Object[]{key,ch,left,right}; },
        in->{ byte[] k=(byte[])in[0]; Object[] c=(Object[])in[1]; int l=(Integer)in[2], r=(Integer)in[3]; return c!=null && l>=0 && l<=r && r+1<=k.length && r+1<=c.length; },
        in->run(()->{ byte[] k=((byte[])in[0]).clone(); Object[] c=in[1]==null?null:((Object[])in[1]).clone(); byte[] out=Orig.sortSmallByteArray(k,c,(Integer)in[2],(Integer)in[3]); return Arrays.toString(out)+"|"+Arrays.toString(c);}),
        in->run(()->{ byte[] k=((byte[])in[0]).clone(); Object[] c=in[1]==null?null:((Object[])in[1]).clone(); byte[] out; switch(rr){case 1: out=P01.sortSmallByteArray(k,c,(Integer)in[2],(Integer)in[3]); break; case 7: out=P07.sortSmallByteArray(k,c,(Integer)in[2],(Integer)in[3]); break; default: out=P15.sortSmallByteArray(k,c,(Integer)in[2],(Integer)in[3]);} return Arrays.toString(out)+"|"+Arrays.toString(c);}),
        in->"key="+Arrays.toString((byte[])in[0])+" children.len="+(in[1]==null?"null":((Object[])in[1]).length)+" left="+in[2]+" right="+in[3]);
    }
    // --- hybridUnsignedBinarySearch rows 3, 9
    for(int row: new int[]{3,9}){ final int rr=row;
      fuzz("row"+row+" hybridUnsignedBinarySearch", 300000, ()->{ int n=R.nextInt(70); char[] a=sortedChars(n, R.nextBoolean()?80:65536); int b=R.nextInt(n+6)-3, e=R.nextInt(n+6)-3; char k=(char)R.nextInt(R.nextBoolean()?80:65536); return new Object[]{a,b,e,k}; },
        in->{ char[] a=(char[])in[0]; int b=(Integer)in[1], e=(Integer)in[2]; return b>=0 && b<=e && e<=a.length; },
        in->run(()->Orig.hybridUnsignedBinarySearch((char[])in[0],(Integer)in[1],(Integer)in[2],(Character)in[3])),
        in->run(()-> rr==3? P03.hybridUnsignedBinarySearch((char[])in[0],(Integer)in[1],(Integer)in[2],(Character)in[3]) : P09.hybridUnsignedBinarySearch((char[])in[0],(Integer)in[1],(Integer)in[2],(Character)in[3])),
        in->"array.len="+((char[])in[0]).length+" begin="+in[1]+" end="+in[2]+" k="+(int)(Character)in[3]);
    }
    // --- advanceUntil row 17
    fuzz("row17 advanceUntil", 300000, ()->{ int n=R.nextInt(70); char[] a=sortedChars(n, R.nextBoolean()?80:65536); int pos=R.nextInt(n+6)-3, len=R.nextInt(n+6)-3; char k=(char)R.nextInt(R.nextBoolean()?80:65536); return new Object[]{a,pos,len,k}; },
        in->{ char[] a=(char[])in[0]; int p=(Integer)in[1], l=(Integer)in[2]; return p>=-1 && l>=0 && l<=a.length && p<l; },
        in->run(()->Orig.advanceUntil((char[])in[0],(Integer)in[1],(Integer)in[2],(Character)in[3])),
        in->run(()->P17.advanceUntil((char[])in[0],(Integer)in[1],(Integer)in[2],(Character)in[3])),
        in->"array="+Arrays.toString(toInts((char[])in[0]))+" pos="+in[1]+" length="+in[2]+" min="+(int)(Character)in[3]);
    // --- intersect rows 10,16,18
    for(int row: new int[]{10,16,18}){ final int rr=row;
      fuzz("row"+row+" unsignedOneSidedGallopingIntersect2by2", 300000, ()->{ int ns=R.nextInt(8), nl=R.nextInt(40); char[] s=sortedChars(ns,R.nextBoolean()?60:65536), l=sortedChars(nl,R.nextBoolean()?60:65536);
            if(R.nextInt(5)==0 && ns>0 && nl>0) l[R.nextInt(nl)]=s[R.nextInt(ns)]; Arrays.sort(l); int sl=R.nextInt(ns+3)-1, ll=R.nextInt(nl+3)-1; return new Object[]{s,sl,l,ll}; },
        in->{ char[] s=(char[])in[0], l=(char[])in[2]; int sl=(Integer)in[1], ll=(Integer)in[3]; return sl>=0 && sl<=s.length && ll>=0 && ll<=l.length && sl<=ll; },
        in->run(()->{ char[] buf=new char[64]; int n=Orig.unsignedOneSidedGallopingIntersect2by2((char[])in[0],(Integer)in[1],(char[])in[2],(Integer)in[3],buf); return n+":"+Arrays.toString(toInts(Arrays.copyOf(buf,Math.max(0,n))));}),
        in->run(()->{ char[] buf=new char[64]; int n= rr==10? P10.unsignedOneSidedGallopingIntersect2by2((char[])in[0],(Integer)in[1],(char[])in[2],(Integer)in[3],buf) : rr==16? P16.unsignedOneSidedGallopingIntersect2by2((char[])in[0],(Integer)in[1],(char[])in[2],(Integer)in[3],buf) : P18.unsignedOneSidedGallopingIntersect2by2((char[])in[0],(Integer)in[1],(char[])in[2],(Integer)in[3],buf); return n+":"+Arrays.toString(toInts(Arrays.copyOf(buf,Math.max(0,n))));}),
        in->"small="+Arrays.toString(toInts((char[])in[0]))+" smallLength="+in[1]+" large="+Arrays.toString(toInts((char[])in[2]))+" largeLength="+in[3]);
    }
  }
  static int[] toInts(char[] c){ int[] r=new int[c.length]; for(int i=0;i<c.length;i++) r[i]=c[i]; return r; }
}
