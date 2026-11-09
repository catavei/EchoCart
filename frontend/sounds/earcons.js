// Short earcons synthesised with Web Audio, so there are no audio files to load.
// listenStart: two rising notes. listenStop: two falling notes.
// success: bright three-note chime. error: low double buzz.
(function () {
  let ctx = null;

  function audio() {
    if (!ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
    }
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  }

  function tone(freq, start, duration, type, gain) {
    const ac = audio();
    if (!ac) return;
    const osc = ac.createOscillator();
    const amp = ac.createGain();
    const t = ac.currentTime + start;
    osc.type = type || "sine";
    osc.frequency.setValueAtTime(freq, t);
    amp.gain.setValueAtTime(0.0001, t);
    amp.gain.exponentialRampToValueAtTime(gain || 0.18, t + 0.015);
    amp.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    osc.connect(amp).connect(ac.destination);
    osc.start(t);
    osc.stop(t + duration + 0.02);
  }

  // Matching vibration patterns on phones, for shoppers who are also hard of hearing.
  function buzz(pattern) {
    // Browsers refuse vibration before the first tap or key press; don't ask until then.
    const active = !navigator.userActivation || navigator.userActivation.hasBeenActive;
    try { if (active && navigator.vibrate) navigator.vibrate(pattern); } catch (e) { /* not supported */ }
  }

  window.Earcons = {
    listenStart() { tone(660, 0, 0.09); tone(880, 0.09, 0.12); buzz(40); },
    listenStop() { tone(880, 0, 0.09); tone(587, 0.09, 0.12); buzz([20, 60, 20]); },
    success() { tone(784, 0, 0.12); tone(988, 0.11, 0.12); tone(1319, 0.22, 0.28); buzz([60, 60, 60, 60, 200]); },
    error() { tone(220, 0, 0.14, "square", 0.08); tone(196, 0.17, 0.2, "square", 0.08); buzz([300, 100, 300]); },
  };
})();
