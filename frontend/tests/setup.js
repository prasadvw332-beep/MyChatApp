// Browser APIs jsdom doesn't have, stubbed for every test file
window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
window.requestAnimationFrame ??= (cb) => setTimeout(cb, 0);
