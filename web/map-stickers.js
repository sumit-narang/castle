/* Castles of Ireland — map layers: castles drawn as die-cut stickers.
 * Each castle shows its own photo as a tilted sticker (from web/stickers/photos/,
 * downloaded by the pipeline's stickers stage); castles without a photo, and
 * clusters without one, use the illustrated sticker for their type.
 * app.js feeds filtered features in through updateMapData().
 */

let mapData = { type: "FeatureCollection", features: [] };

const pointCount = ["get", "point_count"];

function zoomToCluster(e) {
  const f = e.features[0];
  map.getSource("castles").getClusterExpansionZoom(f.properties.cluster_id, (err, zoom) => {
    if (!err) map.easeTo({ center: f.geometry.coordinates, zoom });
  });
}

function bindLayer(id, onClick) {
  map.on("click", id, onClick);
  map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
}

function updateMapData(features) {
  mapData = { type: "FeatureCollection", features };
  map?.getSource("castles")?.setData(mapData);
}

// ---- Stickers: artwork given a die-cut white border and a drop shadow ----
// Any image with a transparent background works (SVG or PNG); point a path at
// different artwork to swap a sticker.
const STICKERS = {
  cluster: "stickers/cluster.svg",
  castle: "stickers/castle.svg",
  tower_house: "stickers/tower_house.svg",
  manor: "stickers/manor.svg",
  fortress: "stickers/fortress.svg",
  motte: "stickers/motte.svg",
};
const STICKER_ART = 176;    // device px the artwork is fitted into
const STICKER_BORDER = 10;  // die-cut border width
const STICKER_PAD = 20;     // room around the border for the shadow
let stickerArt = {};        // name -> ImageData

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`Could not load sticker ${src}`));
    img.src = src;
  });
}

function makeSticker(img) {
  const size = STICKER_ART + 2 * STICKER_PAD;
  const scale = STICKER_ART / Math.max(img.naturalWidth, img.naturalHeight);
  const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
  const x = (size - w) / 2, y = (size - h) / 2;
  const blank = () => Object.assign(document.createElement("canvas"), { width: size, height: size });

  // White silhouette of the artwork...
  const silhouette = blank();
  const sctx = silhouette.getContext("2d");
  sctx.drawImage(img, x, y, w, h);
  sctx.globalCompositeOperation = "source-in";
  sctx.fillStyle = "#fff";
  sctx.fillRect(0, 0, size, size);

  // ...stamped in rings around itself to grow it into the die-cut border.
  const cut = blank();
  const cctx = cut.getContext("2d");
  for (let r = 2; r <= STICKER_BORDER; r += 2) {
    for (let a = 0; a < 360; a += 10) {
      const rad = (a * Math.PI) / 180;
      cctx.drawImage(silhouette, r * Math.cos(rad), r * Math.sin(rad));
    }
  }

  const out = blank();
  const ctx = out.getContext("2d");
  ctx.shadowColor = "rgba(40,25,10,0.35)";
  ctx.shadowBlur = 8;
  ctx.shadowOffsetY = 4;
  ctx.drawImage(cut, 0, 0);
  ctx.shadowColor = "transparent";
  ctx.drawImage(img, x, y, w, h);
  return ctx.getImageData(0, 0, size, size);
}

async function buildStickers() {
  const images = {};
  await Promise.all(Object.entries(STICKERS).map(async ([name, src]) => {
    try {
      images[name] = makeSticker(await loadImage(src));
    } catch (err) {
      console.warn(err.message);
    }
  }));
  return images;
}

async function addStickers() {
  stickerArt = await buildStickers();
  for (const [name, data] of Object.entries(stickerArt)) {
    map.addImage(`st-${name}`, data, { pixelRatio: 2 });
  }
}

// ---- Photo stickers: each castle's own photo as a tilted, die-cut sticker ----
const PHOTO_SIZE = 140;    // device px of the square photo
const PHOTO_BORDER = 11;
const PHOTO_RADIUS = 20;
// Same canvas as the illustrated stickers, so one icon-size suits both.
const PHOTO_CANVAS = STICKER_ART + 2 * STICKER_PAD;
const photoLoading = new Set();
let featureById = null;

// A stable small tilt per castle, so stickers look hand-placed but don't jitter.
function tiltFor(id) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return ((Math.abs(h) % 13) - 6) * (Math.PI / 180);
}

function roundRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function makePhotoSticker(img, props, { stack = false } = {}) {
  const canvas = Object.assign(document.createElement("canvas"), { width: PHOTO_CANVAS, height: PHOTO_CANVAS });
  const ctx = canvas.getContext("2d");
  const half = PHOTO_SIZE / 2, outer = half + PHOTO_BORDER;
  const tilt = tiltFor(props.id);
  ctx.translate(PHOTO_CANVAS / 2, PHOTO_CANVAS / 2 - 4);
  ctx.shadowColor = "rgba(40,25,10,0.35)";
  ctx.shadowBlur = 8;
  ctx.shadowOffsetY = 4;
  ctx.fillStyle = "#fff";

  if (stack) {  // a second card peeking out behind: "there's more here"
    ctx.save();
    ctx.rotate(-tilt - 0.12);
    roundRectPath(ctx, -outer, -outer, outer * 2, outer * 2, PHOTO_RADIUS + PHOTO_BORDER);
    ctx.fillStyle = "#f3ece0";
    ctx.fill();
    ctx.restore();
  }

  ctx.rotate(tilt);
  roundRectPath(ctx, -outer, -outer, outer * 2, outer * 2, PHOTO_RADIUS + PHOTO_BORDER);
  ctx.fill();
  ctx.shadowColor = "transparent";

  // Centre-cropped square photo.
  ctx.save();
  roundRectPath(ctx, -half, -half, PHOTO_SIZE, PHOTO_SIZE, PHOTO_RADIUS);
  ctx.clip();
  ctx.imageSmoothingQuality = "high";
  const side = Math.min(img.naturalWidth, img.naturalHeight);
  ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side,
    -half, -half, PHOTO_SIZE, PHOTO_SIZE);
  ctx.restore();

  if (!stack) {  // type badge in the corner, in the legend colour
    ctx.beginPath();
    ctx.arc(half - 6, half - 6, 13, 0, 2 * Math.PI);
    ctx.fillStyle = COLOR_BY_CAT[props.category] || "#999";
    ctx.fill();
    ctx.lineWidth = 4;
    ctx.strokeStyle = "#fff";
    ctx.stroke();
  }
  return ctx.getImageData(0, 0, PHOTO_CANVAS, PHOTO_CANVAS);
}

// Photo stickers are named "ph-<castle id>" (single castle) or "phc-<castle id>"
// (fronting a cluster) and built the first time the map asks for one. The image
// is added only once ready: Mapbox then re-lays out just the tiles that asked
// for it. (Swapping a placeholder with updateImage doesn't reliably reach tiles
// that are already laid out.) If the photo can't load, the illustration is used.
function onStyleImageMissing(e) {
  const m = /^(phc?)-(.+)$/.exec(e.id);
  if (!m || photoLoading.has(e.id)) return;

  featureById ||= new Map(allFeatures.map((f) => [f.properties.id, f.properties]));
  const props = featureById.get(m[2]);
  const stack = m[1] === "phc";
  const fallback = () => stickerArt[stack ? "cluster" : props?.category] || stickerArt.castle;
  const add = (data) => {
    if (data && !map.hasImage(e.id)) map.addImage(e.id, data, { pixelRatio: 2 });
  };
  if (!props?.sticker) return add(fallback());

  photoLoading.add(e.id);
  loadImage(props.sticker)
    .then((img) => add(makePhotoSticker(img, props, { stack })))
    .catch((err) => {
      console.warn(err.message);
      add(fallback());
    })
    .finally(() => photoLoading.delete(e.id));
}

// ---- Layers ----
async function addCastleLayers() {
  map.addSource("castles", {
    type: "geojson",
    data: mapData,
    cluster: true,
    clusterRadius: 60,
    clusterMaxZoom: 12,
    clusterProperties: {
      // "score|id" of the castle that fronts each cluster: one with a photo
      // first, then one with a Wikipedia article.
      rep: [
        ["case", [">", ["accumulated"], ["get", "rep"]], ["accumulated"], ["get", "rep"]],
        ["concat", ["to-string", ["+",
          ["case", ["to-boolean", ["get", "sticker"]], 2, 0],
          ["case", ["get", "has_article"], 1, 0]]], "|", ["get", "id"]],
      ],
    },
  });
  await addStickers();

  const repId = ["slice", ["get", "rep"], 2];
  const repHasPhoto = [">=", ["to-number", ["slice", ["get", "rep"], 0, 1]], 2];
  map.addLayer({
    id: "sticker-clusters", type: "symbol", source: "castles", filter: ["has", "point_count"],
    layout: {
      "icon-image": ["case", repHasPhoto, ["concat", "phc-", repId], "st-cluster"],
      "icon-size": ["interpolate", ["linear"], pointCount, 2, 0.5, 150, 0.85],
      "icon-anchor": "bottom",
      "icon-allow-overlap": true,
      "symbol-sort-key": ["-", pointCount],
      "text-field": ["get", "point_count_abbreviated"],
      "text-font": ["DIN Pro Bold", "Arial Unicode MS Bold"],
      "text-size": 13,
      "text-anchor": "top",
      "text-offset": [0, -0.1],
      "text-allow-overlap": true,
    },
    paint: { "text-color": "#fffaf2", "text-halo-color": "#3b2f25", "text-halo-width": 2.5 },
  });
  map.addLayer({
    id: "sticker-points", type: "symbol", source: "castles", filter: ["!", ["has", "point_count"]],
    layout: {
      "icon-image": ["case", ["to-boolean", ["get", "sticker"]],
        ["concat", "ph-", ["get", "id"]], ["concat", "st-", ["get", "category"]]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 7, 0.4, 13, 0.7],
      "icon-anchor": "bottom",
      "icon-allow-overlap": true,
    },
  });
  bindLayer("sticker-clusters", zoomToCluster);
  bindLayer("sticker-points", (e) => openPopup(e.features[0]));
}

function initMapLayers() {
  map.on("styleimagemissing", onStyleImageMissing);
  map.on("load", addCastleLayers);
}
