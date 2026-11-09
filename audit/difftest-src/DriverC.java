import java.util.*; import java.util.function.*;
public class DriverC { static Random R=new Random(3);
 static String run(Supplier<Object> s){ try{ return String.valueOf(s.get()); }catch(Throwable t){ return "EXC:"+t.getClass().getSimpleName(); } }
 public static void main(String[] a){ for(int row: new int[]{2,8}){ int[] vn={0,0}, mn={0,0}; Map<String,String> wit=new LinkedHashMap<>();
   for(int t=0;t<400000;t++){ long[] bm=new long[1024]; int mode=R.nextInt(4); for(int i=0;i<1024;i++) bm[i]= mode==0?0L: mode==1?-1L: mode==2? R.nextLong() : (R.nextInt(50)==0? R.nextLong():0L);
     int pop=0; for(long w:bm) pop+=Long.bitCount(w); int card = R.nextInt(6)==0? -1 : R.nextInt(8)==0? R.nextInt(65537) : pop;
     int s=R.nextInt(65536+200)-100, e=R.nextInt(65536+200)-100; if(R.nextBoolean()){ s=R.nextInt(65537); e=s+R.nextInt(65537-s);} 
     boolean valid = s>=0 && s<=e && e<=65536 && (card==-1 || card==pop);
     final long[] fb=bm; final int fc=card, fs=s, fe=e; String o=run(()->new CO(fb,fc).cardinalityInRange(fs,fe)); String p= row==2? run(()->new C02(fb,fc).cardinalityInRange(fs,fe)) : run(()->new C08(fb,fc).cardinalityInRange(fs,fe));
     int[] c= valid?vn:mn; c[0]++; if(!o.equals(p)){ c[1]++; String k=(valid?"valid":"malformed")+" "+(o.startsWith("EXC")?o:"VALUE")+"->"+(p.startsWith("EXC")?p:"VALUE"); if(!wit.containsKey(k)) wit.put(k,"card="+card+" pop="+pop+" start="+s+" end="+e+" orig="+o+" patched="+p);} }
   System.out.println("### row"+row+" cardinalityInRange valid diffs="+vn[1]+"/"+vn[0]+" malformed diffs="+mn[1]+"/"+mn[0]); for(String k:wit.keySet()) System.out.println("   witness["+k+"] "+wit.get(k)); } } }
