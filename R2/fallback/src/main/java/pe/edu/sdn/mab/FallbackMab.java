/* SPDX-License-Identifier: Apache-2.0
 * VNRT adaptation of the existing pe.edu.sdn.fallback-mab component.
 */
package pe.edu.sdn.mab;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.Arrays;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import org.onlab.packet.Ethernet;
import org.onlab.packet.MacAddress;
import org.onosproject.core.ApplicationId;
import org.onosproject.core.CoreService;
import org.onosproject.net.ConnectPoint;
import org.onosproject.net.flow.DefaultTrafficSelector;
import org.onosproject.net.packet.PacketContext;
import org.onosproject.net.packet.PacketPriority;
import org.onosproject.net.packet.PacketProcessor;
import org.onosproject.net.packet.PacketService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class FallbackMab {
    private static final Logger log = LoggerFactory.getLogger(FallbackMab.class);
    protected CoreService coreService;
    protected PacketService packetService;
    private ApplicationId appId;
    private final PacketProcessor processor = this::process;
    private ExecutorService executor;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(3)).build();
    private final Map<String, Long> seen = new ConcurrentHashMap<>();
    private final Set<String> accessPorts = Arrays.stream(System.getenv().getOrDefault(
            "MAB_ACCESS_PORTS", "of:0000e6925d87354b/1,of:0000766a0c84c741/1").split(","))
            .map(String::trim).collect(Collectors.toSet());
    private String endpoint;

    protected void activate() {
        endpoint = System.getenv().getOrDefault("MAB_MAPPER_URL",
                "http://192.168.201.1:5000/api/mab-request");
        executor = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(32), new ThreadPoolExecutor.AbortPolicy());
        appId = coreService.registerApplication("pe.edu.sdn.fallback-mab");
        // Solo observa: debe ejecutarse antes de que el resolvedor ARP marque
        // el contexto como atendido, sin emitir ni bloquear la trama original.
        packetService.addProcessor(processor, PacketProcessor.advisor(2));
        packetService.requestPackets(DefaultTrafficSelector.builder()
                .matchEthType(Ethernet.TYPE_ARP).build(), PacketPriority.REACTIVE, appId);
        log.info("Fallback MAB VNRT started with bounded queue and expiring cache");
    }

    protected void deactivate() {
        packetService.cancelPackets(DefaultTrafficSelector.builder()
                .matchEthType(Ethernet.TYPE_ARP).build(), PacketPriority.REACTIVE, appId);
        packetService.removeProcessor(processor);
        if (executor != null) {
            executor.shutdownNow();
        }
        seen.clear();
        log.info("Fallback MAB VNRT stopped");
    }

    private void process(PacketContext context) {
        if (context.isHandled() || context.inPacket() == null) {
            return;
        }
        Ethernet ethernet = context.inPacket().parsed();
        if (ethernet == null || ethernet.getEtherType() != Ethernet.TYPE_ARP) {
            return;
        }
        MacAddress mac = ethernet.getSourceMAC();
        if (mac.isMulticast()) {
            return;
        }
        ConnectPoint point = context.inPacket().receivedFrom();
        String device = point.deviceId().toString();
        String port = point.port().toString();
        if (!accessPorts.contains(device + "/" + port)) {
            return;
        }
        String key = mac + "@" + device + "/" + port;
        long now = System.currentTimeMillis();
        if (seen.size() >= 4096) {
            seen.entrySet().removeIf(e -> e.getValue() <= now);
            if (seen.size() >= 4096) {
                return;
            }
        }
        Long previous = seen.putIfAbsent(key, now + 15000);
        if (previous != null && (previous > now || !seen.replace(key, previous, now + 15000))) {
            return;
        }
        try {
            executor.submit(() -> send(mac.toString(), device, port, key));
        } catch (java.util.concurrent.RejectedExecutionException error) {
            // No blocks, emits or retries on the packet-processing thread.
            seen.put(key, now + 5000);
        }
    }

    private void send(String mac, String device, String port, String key) {
        try {
            String body = String.format("{\"mac\":\"%s\",\"deviceId\":\"%s\",\"port\":\"%s\"}",
                    mac, device, port);
            HttpRequest request = HttpRequest.newBuilder(URI.create(endpoint))
                    .version(HttpClient.Version.HTTP_1_1)
                    .header("Content-Type", "application/json")
                    .timeout(Duration.ofSeconds(10))
                    .POST(HttpRequest.BodyPublishers.ofString(body)).build();
            HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
            seen.put(key, System.currentTimeMillis() + (response.statusCode() == 200 ? 30000 : 5000));
            log.info("MAB lookup mac={} device={} port={} HTTP={}", mac, device, port, response.statusCode());
        } catch (Exception error) {
            seen.put(key, System.currentTimeMillis() + 5000);
            log.warn("MAB lookup failed for {}: {}", mac, error.getClass().getSimpleName());
        }
    }
}
