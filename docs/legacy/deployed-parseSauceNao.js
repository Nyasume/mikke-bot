"use strict";

const Markup = require('telegraf/markup');
const urlbase = require("../settings/settings.js").url;
const MESSAGE = require("../settings/settings.js").msg;
const tools = require("../tools/tools.js");
const analytics = require('./analytics.js');
const reportToOwner = require("./reportToOwner.js");

// button captions for ext_urls, by host
const siteNames = {
  "pixiv.net": "Pixiv",
  "danbooru.donmai.us": "Danbooru",
  "gelbooru.com": "Gelbooru",
  "sankakucomplex.com": "Sankaku",
  "anime-pictures.net": "Anime-Pictures",
  "imdb.com": "IMDb",
  "anidb.net": "AniDB",
  "e621.net": "e621",
  "yande.re": "yandere",
  "konachan.com": "Konachan",
  "deviantart.com": "deviantArt",
  "twitter.com": "Twitter",
  "x.com": "Twitter",
  "artstation.com": "ArtStation",
  "mangadex.org": "MangaDex",
  "mangaupdates.com": "MangaUpdates",
  "myanimelist.net": "MAL",
  "anilist.co": "AniList",
  "fanbox.cc": "Fanbox",
  "fantia.jp": "Fantia",
  "nijie.info": "Nijie",
  "furaffinity.net": "FurAffinity",
  "e-hentai.org": "E-Hentai",
  "pawoo.net": "Pawoo",
  "drawr.net": "Drawr",
  "bcy.net": "BCY"
};

const siteName = url => {
  let host;
  try {
    host = new URL(url).hostname.replace(/^www\./, "");
  } catch (e) {
    return null;
  }
  for (const domain in siteNames)
    if (host == domain || host.endsWith("." + domain))
      return siteNames[domain];
  return host;
};

const isUrl = s => typeof s == "string" && /^https?:\/\//.test(s);

const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const joined = v => Array.isArray(v) ? v.join(", ") : v;

// response is the raw JSON text of search.php?output_type=2
const parseSauceNao = (response, bot, editMsg) => {
  console.log("get saucenao completed ");
  let json;
  try {
    json = JSON.parse(response);
  } catch (e) {
    json = null;
  }
  if (json && json.header && json.header.status != 0)
    console.log("saucenao status", json.header.status, json.header.message);

  const minSimilarity = urlbase.sauceNaoParams.minSimilarity;
  const tolerance = urlbase.sauceNaoParams.tolerance;
  const results = [];
  for (const r of (json && json.results) || []) {
    const percent = parseFloat(r.header.similarity);
    if (percent < minSimilarity)
      continue;
    if (results.length && Math.abs(results[0].percent - percent) > tolerance)
      continue;
    results.push({percent: percent, data: r.data || {}});
  }

  if (!results.length){
    analytics.track(editMsg.origFrom, "sauce_not_found", {url: editMsg.url});
    return Promise.reject(new Error(MESSAGE.zeroResult));
  }

  // first non-empty value across the matched results wins
  const first = pick => {
    for (const r of results) {
      const v = joined(pick(r.data));
      if (v)
        return v;
    }
    return undefined;
  };

  let title = first(d => d.title || (!isUrl(d.source) && d.source) || d.eng_name || d.jp_name);
  const anidbResult = results.find(r => r.data.anidb_aid);
  let part = first(d => d.part);
  if (title && anidbResult && part) {
    title += " (Ep. " + part + ")";
    part = undefined;
  }

  const content = [
    ["Character", first(d => d.characters)],
    ["Material", first(d => d.material)],
    ["By", first(d => d.creator || d.member_name || d.author_name ||
      (d.twitter_user_handle && "@" + d.twitter_user_handle))],
    ["Part", part],
    ["Year", first(d => d.year)],
    ["Time", first(d => d.est_time)]
  ];

  let displayText = "";
  if (title)
    displayText = "<b>" + esc(title) + "</b>" + '\n';
  for (const [key, value] of content)
    if (value)
      displayText += "<b>" + key + ": </b>" + esc(value) + '\n';
  if (!displayText.trim())
    displayText = "-no title-";

  const links = {};
  for (const r of results) {
    for (const url of r.data.ext_urls || []) {
      const name = siteName(url);
      if (name && !links[name])
        links[name] = url;
    }
    if (isUrl(r.data.source) && !links.Source)
      links.Source = r.data.source;
  }
  if (anidbResult)
    links.MAL = urlbase.mal + tools.json2query({q: first(d => !isUrl(d.source) && d.source) || title});

  const bList = [];
  for (const key in links) {
    if (bList.length >= 6) //max 6 buttons or 3 lines
      break;
    console.log("link:", key, links[key]);
    bList.push(Markup.urlButton(bList.length ? key : "View on " + key, links[key]));
  }

  // no links -> no keyboard instead of an empty [[]] one
  const markup = bList.length ? Markup.inlineKeyboard(tools.buttonsGridify(bList)) : undefined;

  analytics.track(editMsg.origFrom, "sauce_found_saucenao");

  let report = "<a href=\"" + urlbase.sauceNao + "url=" +
    editMsg.url + "\">link</a>\n\n" + displayText;
  reportToOwner.sauceNaoResult(report, bot);
  reportToOwner.reportFile(editMsg.fileId, bot, 1);
  return [displayText, markup, false];
};

module.exports = parseSauceNao;
