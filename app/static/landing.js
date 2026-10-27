// Enlaces viejos de la app (/#/trabajo/<id>, del bot o de favoritos) siguen funcionando.
if (location.hash.startsWith("#/")) location.replace("/app/" + location.hash);

document.addEventListener("DOMContentLoaded", function () {

  var demo = document.getElementById("demo");
  var recibo = document.getElementById("recibo");
  var SCAN_START = 1150, SCAN_MS = 1500;

  // Each highlight fires when the scan band passes it. Layout is read once, before anything moves.
  function programar() {
    var alto = recibo.offsetHeight;
    recibo.querySelectorAll(".dato").forEach(function (d) {
      var y = (d.offsetTop + d.offsetHeight) / alto;
      d.style.setProperty("--t", Math.round(SCAN_START + SCAN_MS * y) + "ms");
    });
  }
  function reproducir() {
    demo.classList.remove("reproducir");
    programar();
    void demo.offsetWidth;            // restart the CSS timeline
    demo.classList.add("reproducir");
  }
  document.getElementById("replay").addEventListener("click", reproducir);
  // Wait for the web fonts: a font swap mid-animation reflows the ticket and causes the stutter.
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(function () {
    requestAnimationFrame(reproducir);
  });
});
