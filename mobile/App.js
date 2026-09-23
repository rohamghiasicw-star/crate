/* Addify in Expo Go.
 *
 * WHY A WEBVIEW AND NOT A REWRITE. The whole product is one HTML file that the engine
 * already serves, and it is the thing Konnor has been testing all week. Re-implementing
 * it in React Native would take days, would immediately drift from the web build, and
 * would test something different from what ships. This hosts the real UI, so what he
 * sees here is exactly what the engine serves.
 *
 * WHY THIS ROUTE AT ALL. TestFlight, ad hoc and a standalone build all need the paid
 * Apple Developer Program. Expo Go does not: it is a free App Store download and it
 * opens a project from a link. That is how WYA was tested and it costs nothing.
 */
import React, { useRef, useState, useCallback } from 'react';
import {
  ActivityIndicator, Linking, RefreshControl, SafeAreaView,
  ScrollView, StatusBar, StyleSheet, Text, TouchableOpacity, View,
} from 'react-native';
import { WebView } from 'react-native-webview';
import Constants from 'expo-constants';

/* THE ENGINE ADDRESS IS RESOLVED AT LAUNCH, NOT BAKED IN.
   The engine sits behind a free tunnel whose hostname changes roughly hourly, so a URL
   compiled into the app is wrong within the hour and the tester just sees "can't reach
   Addify". The watchdog publishes each new hostname to a public gist, which is a fixed
   address, so the app asks that first and only falls back to the compiled-in value if the
   lookup fails. A build from this morning still works tonight. */
const DIRECTORY = 'https://gist.githubusercontent.com/rohamghiasicw-star/d63fcb85b88d9a8f12e943605dd0a078/raw/engine-url.txt';
const BAKED = (Constants.expoConfig?.extra?.engine || '').replace(/\/+$/, '');

async function resolveEngine() {
  try {
    // cache-bust: raw gist responses cache hard, and a stale hostname is the exact
    // failure this exists to prevent.
    const r = await fetch(DIRECTORY + '?t=' + Date.now(), { cache: 'no-store' });
    if (r.ok) {
      const u = (await r.text()).trim().replace(/\/+$/, '');
      if (/^https?:\/\//.test(u)) return u;
    }
  } catch (e) { /* offline or gist down; the baked value is the fallback */ }
  return BAKED;
}
const INK = '#F4F4F5', DIM = '#9A9AA6', BG = '#150E33', ACCENT = '#7C8CFF';

export default function App() {
  const ref = useRef(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [key, setKey] = useState(0);
  const [engine, setEngine] = useState(null);

  // Re-resolve on every retry, not only at boot. If the tunnel moved while the app was
  // open, retrying against the old address would fail forever.
  React.useEffect(() => {
    let alive = true;
    resolveEngine().then(u => { if (alive) setEngine(u); });
    return () => { alive = false; };
  }, [key]);

  const retry = useCallback(() => {
    setFailed(false); setLoading(true); setEngine(null); setKey(k => k + 1);
  }, []);

  if (!engine) {
    return (
      <SafeAreaView style={s.fill}>
        <StatusBar barStyle="light-content" />
        <View style={s.overlay}><ActivityIndicator size="large" color={ACCENT} /></View>
      </SafeAreaView>
    );
  }

  // The engine runs on a laptop behind a tunnel, so "unreachable" is a normal state and
  // not a crash. Say so plainly and offer the one useful action instead of a white screen.
  if (failed) {
    return (
      <SafeAreaView style={s.fill}>
        <StatusBar barStyle="light-content" />
        <ScrollView contentContainerStyle={s.mid}
          refreshControl={<RefreshControl refreshing={false} onRefresh={retry} tintColor={DIM} />}>
          <Text style={s.h}>Can't reach Addify</Text>
          <Text style={s.p}>
            The engine runs on Roham's Mac. If it's asleep or the tunnel moved, this is what
            you get. Pull down to retry.
          </Text>
          <Text style={s.url}>{engine || BAKED || 'no engine configured'}</Text>
          <TouchableOpacity style={s.btn} onPress={retry}>
            <Text style={s.btnText}>Try again</Text>
          </TouchableOpacity>
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={s.fill}>
      <StatusBar barStyle="light-content" />
      <WebView
        key={key}
        ref={ref}
        source={{ uri: engine }}
        style={s.fill}
        // Dark, so a slow first paint does not flash white over a dark app.
        containerStyle={{ backgroundColor: BG }}
        allowsInlineMediaPlayback
        mediaPlaybackRequiresUserAction={false}
        originWhitelist={['*']}
        onLoadEnd={() => setLoading(false)}
        onError={() => { setLoading(false); setFailed(true); }}
        onHttpError={(e) => {
          // A tunnel that is up but pointing at a dead engine answers 502/503. That is the
          // unreachable case too, not a page the user should be shown.
          const c = e?.nativeEvent?.statusCode;
          if (c >= 500) { setLoading(false); setFailed(true); }
        }}
        // Anything that is not the engine (SoundCloud, YouTube, Spotify) opens in the real
        // app or Safari rather than trapping the user inside this WebView with no back.
        onShouldStartLoadWithRequest={(req) => {
          if (req.url.startsWith(engine) || req.url.startsWith('about:')) return true;
          Linking.openURL(req.url).catch(() => {});
          return false;
        }}
        pullToRefreshEnabled
      />
      {loading && (
        <View style={s.overlay} pointerEvents="none">
          <ActivityIndicator size="large" color={ACCENT} />
        </View>
      )}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  fill: { flex: 1, backgroundColor: BG },
  mid: { flexGrow: 1, justifyContent: 'center', padding: 28 },
  h: { color: INK, fontSize: 22, fontWeight: '700', marginBottom: 10 },
  p: { color: DIM, fontSize: 15, lineHeight: 22 },
  url: { color: DIM, fontSize: 12, marginTop: 16, opacity: 0.7 },
  btn: { marginTop: 22, backgroundColor: ACCENT, paddingVertical: 14,
         borderRadius: 14, alignItems: 'center' },
  btnText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  overlay: { ...StyleSheet.absoluteFillObject, alignItems: 'center',
             justifyContent: 'center', backgroundColor: BG },
});
