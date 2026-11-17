/* SPDX-License-Identifier: Apache-2.0 */
package pe.edu.sdn.mab;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.List;
import java.util.Map;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import org.onlab.packet.Ethernet;
import org.onosproject.net.ConnectPoint;
import org.onosproject.net.DeviceId;
import org.onosproject.net.PortNumber;
import org.onosproject.net.packet.InboundPacket;
import org.onosproject.net.packet.PacketContext;

public final class FallbackMabSafetyTest {
    static final class CapturingExecutor extends AbstractExecutorService {
        int count;
        boolean reject;
        public void execute(Runnable runnable) {
            if (reject) { throw new RejectedExecutionException(); }
            count++; // Nunca ejecutar HTTP en estas pruebas.
        }
        public void shutdown() { }
        public List<Runnable> shutdownNow() { return List.of(); }
        public boolean isShutdown() { return false; }
        public boolean isTerminated() { return false; }
        public boolean awaitTermination(long n, TimeUnit unit) { return true; }
    }
    static PacketContext context(short type, byte first, int port, boolean handled) {
        return contextAt(type, first, port, handled, "of:0000e6925d87354b");
    }
    static PacketContext contextAt(short type, byte first, int port, boolean handled, String device) {
        Ethernet eth = new Ethernet().setEtherType(type)
                .setSourceMACAddress(new byte[]{first, 17, 34, 51, 68, 85});
        ConnectPoint point = new ConnectPoint(DeviceId.deviceId(device), PortNumber.portNumber(port));
        InboundPacket packet = (InboundPacket) Proxy.newProxyInstance(InboundPacket.class.getClassLoader(),
                new Class[]{InboundPacket.class}, (p, m, a) -> {
                    if (m.getName().equals("parsed")) { return eth; }
                    if (m.getName().equals("receivedFrom")) { return point; }
                    throw new AssertionError("Unexpected packet action: " + m.getName());
                });
        return (PacketContext) Proxy.newProxyInstance(PacketContext.class.getClassLoader(),
                new Class[]{PacketContext.class}, (p, m, a) -> {
                    if (m.getName().equals("isHandled")) { return handled; }
                    if (m.getName().equals("inPacket")) { return packet; }
                    throw new AssertionError("Unexpected context action: " + m.getName());
                });
    }
    static void check(boolean ok, String name) {
        if (!ok) { throw new AssertionError(name); }
        System.out.println("PASS " + name);
    }
    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        FallbackMab app = new FallbackMab();
        CapturingExecutor executor = new CapturingExecutor();
        Field field = FallbackMab.class.getDeclaredField("executor"); field.setAccessible(true); field.set(app, executor);
        Field cache = FallbackMab.class.getDeclaredField("seen"); cache.setAccessible(true);
        Map<String,Long> seen = (Map<String,Long>) cache.get(app);
        Method process = FallbackMab.class.getDeclaredMethod("process", PacketContext.class); process.setAccessible(true);
        PacketContext arp=context(Ethernet.TYPE_ARP,(byte)2,1,false);
        process.invoke(app,arp); process.invoke(app,arp);
        check(executor.count==1,"duplicate ARP is suppressed without emitting packets");
        seen.replaceAll((k,v)->0L); process.invoke(app,arp);
        check(executor.count==2,"expired MAC can retry");
        process.invoke(app,contextAt(Ethernet.TYPE_ARP,(byte)2,1,false,"of:0000766a0c84c741"));
        check(executor.count==3,"same MAC on another access is independently checked");
        process.invoke(app,context(Ethernet.TYPE_ARP,(byte)2,2,false));
        check(executor.count==3,"trunk or non access port ignored");
        process.invoke(app,context(Ethernet.TYPE_ARP,(byte)3,1,false));
        check(executor.count==3,"multicast source ignored");
        process.invoke(app,context(Ethernet.TYPE_ARP,(byte)2,3,true));
        check(executor.count==3,"handled packet ignored");
        process.invoke(app,context(Ethernet.TYPE_IPV4,(byte)2,3,false));
        check(executor.count==3,"non ARP ignored");
        seen.clear(); executor.reject=true; process.invoke(app,arp);
        check(executor.count==3 && seen.size()==1,"full queue fails without packet output or blocking");
        executor.reject=false; seen.clear();
        for (int i=0;i<4096;i++) { seen.put("test-"+i,System.currentTimeMillis()+30000); }
        process.invoke(app,arp);
        check(executor.count==3,"cache capacity bounded");
        seen.replaceAll((k,v)->0L); process.invoke(app,arp);
        check(executor.count==4 && seen.size()==1,"expired cache entries pruned");
        System.out.println("10 safety tests passed; no HTTP tasks executed");
    }
}
