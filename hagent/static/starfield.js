// Interstellar background: sparse, slow-drifting stars with rare comets.
// Same tuning as the Holly voice app's starfield (holly_voice.html) -- kept
// as a shared visual motif between the two projects. Runs as a fixed
// full-viewport canvas behind all page content (see .starfield-canvas in
// style.css for positioning/z-index).
(function starfield() {
    const canvas = document.getElementById('starsCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    let stars = [];

    function layout() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        const count = Math.floor((canvas.width * canvas.height) / 3500);
        const angle = Math.random() * Math.PI * 2;
        stars = Array.from({ length: count }, () => {
            const depth = Math.random();
            const r = 0.5 + depth * 1.6;
            const drift = 0.015 + depth * 0.05;
            return {
                x: Math.random() * canvas.width,
                y: Math.random() * canvas.height,
                r,
                glow: r > 1.3,
                baseAlpha: 0.2 + depth * 0.35,
                vx: Math.cos(angle) * drift,
                vy: Math.sin(angle) * drift,
                nextShine: 4 + Math.random() * 14,
                shineT: -1,
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
        const t = now / 1000;
        const dt = last === null ? 0 : (now - last) / 1000;
        last = now;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        for (const s of stars) {
            s.x += s.vx * dt;
            s.y += s.vy * dt;
            if (s.x < 0) s.x += canvas.width;
            else if (s.x > canvas.width) s.x -= canvas.width;
            if (s.y < 0) s.y += canvas.height;
            else if (s.y > canvas.height) s.y -= canvas.height;

            let shine = 0;
            if (s.shineT >= 0) {
                s.shineT += dt;
                const dur = 1.4;
                if (s.shineT >= dur) {
                    s.shineT = -1;
                    s.nextShine = 4 + Math.random() * 14;
                } else {
                    shine = Math.sin((s.shineT / dur) * Math.PI);
                }
            } else {
                s.nextShine -= dt;
                if (s.nextShine <= 0) s.shineT = 0;
            }
            ctx.globalAlpha = s.baseAlpha + shine * (1 - s.baseAlpha);
            ctx.fillStyle = '#ffffff';
            if (s.glow) {
                ctx.shadowColor = '#ffffff';
                ctx.shadowBlur = s.r * 3;
            } else {
                ctx.shadowBlur = 0;
            }
            ctx.beginPath();
            ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
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
            grad.addColorStop(0, 'rgba(255,255,255,0)');
            grad.addColorStop(1, `rgba(255,255,255,${0.85 * fade})`);
            ctx.strokeStyle = grad;
            ctx.lineWidth = 1.4;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(tailX, tailY);
            ctx.lineTo(comet.x, comet.y);
            ctx.stroke();

            ctx.globalAlpha = fade;
            ctx.fillStyle = '#ffffff';
            ctx.shadowColor = '#ffffff';
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
