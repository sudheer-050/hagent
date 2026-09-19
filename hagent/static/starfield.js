// Interstellar background: sparse stars that drift slowly and keep a stable layout across reloads.
(function starfield() {
    const canvas = document.getElementById('starsCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const storageKey = 'hagent-starfield-v1';

    function loadContinuity() {
        try {
            const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
            if (saved && Number.isInteger(saved.seed) && Number.isFinite(saved.epoch)) return saved;
            const created = { seed: Math.floor(Math.random() * 0xffffffff), epoch: Date.now() };
            localStorage.setItem(storageKey, JSON.stringify(created));
            return created;
        } catch (_) {
            return { seed: 0x48414745, epoch: Date.UTC(2025, 0, 1) };
        }
    }

    const continuity = loadContinuity();
    const reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    let stars = [];

    function randomGenerator(seed) {
        let state = seed | 0;
        return function () {
            state = (state + 0x6D2B79F5) | 0;
            let value = state;
            value = Math.imul(value ^ (value >>> 15), value | 1);
            value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
            return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
        };
    }

    function wrap(value, size) {
        return ((value % size) + size) % size;
    }

    function layout() {
        canvas.width = Math.max(1, window.innerWidth);
        canvas.height = Math.max(1, window.innerHeight);
        const count = Math.floor((canvas.width * canvas.height) / 3500);
        const random = randomGenerator(continuity.seed);
        const direction = random() * Math.PI * 2;
        stars = Array.from({ length: count }, () => {
            const depth = random();
            const r = 0.5 + depth * 1.6;
            const speed = reducedMotion ? 0 : 0.003 + depth * 0.009;
            return {
                u: random(),
                v: random(),
                r,
                glow: r > 1.3,
                color: random() < 0.22 ? '#a9d5ff' : '#e8f1ff',
                baseAlpha: 0.2 + depth * 0.35,
                vx: Math.cos(direction) * speed,
                vy: Math.sin(direction) * speed,
                twinkleOffset: random() * 20,
                twinklePeriod: 9 + random() * 15,
            };
        });
    }
    layout();
    window.addEventListener('resize', layout);

    let comet = null;
    let nextCometIn = 30 + Math.random() * 90;
    function spawnComet(cw, ch) {
        const midX = cw * 0.5, midY = ch * 0.5;
        const spanX = cw * 0.3, spanY = ch * 0.25;
        const startAngle = Math.random() * Math.PI * 2;
        const travel = Math.max(cw, ch) * 0.55;
        const dir = startAngle + Math.PI + (Math.random() * 0.6 - 0.3);
        const x0 = midX + Math.cos(startAngle) * (spanX * (0.3 + Math.random() * 0.7));
        const y0 = midY + Math.sin(startAngle) * (spanY * (0.3 + Math.random() * 0.7));
        const duration = 1.1 + Math.random() * 0.6;
        return {
            x: x0, y: y0,
            vx: Math.cos(dir) * travel / duration,
            vy: Math.sin(dir) * travel / duration,
            age: 0,
            duration,
        };
    }

    let last = null;
    function frame(now) {
        const dt = last === null ? 0 : Math.min((now - last) / 1000, 0.1);
        last = now;
        const elapsed = Math.max(0, (Date.now() - continuity.epoch) / 1000);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        for (const s of stars) {
            const x = wrap(s.u * canvas.width + s.vx * elapsed, canvas.width);
            const y = wrap(s.v * canvas.height + s.vy * elapsed, canvas.height);
            const phase = reducedMotion ? 0 : (elapsed + s.twinkleOffset) % s.twinklePeriod;
            const shine = phase < 1.4 ? Math.sin((phase / 1.4) * Math.PI) : 0;
            ctx.globalAlpha = s.baseAlpha + shine * (1 - s.baseAlpha);
            ctx.fillStyle = s.color;
            ctx.shadowBlur = s.glow ? s.r * 3 : 0;
            if (s.glow) ctx.shadowColor = s.color;
            ctx.beginPath();
            ctx.arc(x, y, s.r, 0, Math.PI * 2);
            ctx.fill();
        }
        ctx.shadowBlur = 0;
        ctx.globalAlpha = 1;

        if (comet === null) {
            nextCometIn -= dt;
            if (nextCometIn <= 0) {
                comet = spawnComet(canvas.width, canvas.height);
                nextCometIn = 120 + Math.random() * 240;
            }
        } else {
            comet.age += dt;
            comet.x += comet.vx * dt;
            comet.y += comet.vy * dt;
            const p = comet.age / comet.duration;
            const fade = p < 0.15 ? p / 0.15 : (1 - (p - 0.15) / 0.85);
            const tailX = comet.x - comet.vx * 0.12;
            const tailY = comet.y - comet.vy * 0.12;
            const grad = ctx.createLinearGradient(tailX, tailY, comet.x, comet.y);
            grad.addColorStop(0, 'rgba(179,219,255,0)');
            grad.addColorStop(1, `rgba(214,234,255,${0.85 * fade})`);
            ctx.strokeStyle = grad;
            ctx.lineWidth = 1.4;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(tailX, tailY);
            ctx.lineTo(comet.x, comet.y);
            ctx.stroke();

            ctx.globalAlpha = fade;
            ctx.fillStyle = '#e8f1ff';
            ctx.shadowColor = '#a9d5ff';
            ctx.shadowBlur = 6;
            ctx.beginPath();
            ctx.arc(comet.x, comet.y, 1.3, 0, Math.PI * 2);
            ctx.fill();
            ctx.shadowBlur = 0;
            ctx.globalAlpha = 1;

            if (comet.age >= comet.duration || comet.x < -50 || comet.x > canvas.width + 50 || comet.y < -50 || comet.y > canvas.height + 50) {
                comet = null;
            }
        }

        requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
})();
