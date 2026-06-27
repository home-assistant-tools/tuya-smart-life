#!/usr/bin/env node
const crypto = require("crypto");
const zlib = require("zlib");

const SIGN_KEYS = new Set([
  "a", "v", "lat", "lon", "lang", "deviceId", "appVersion", "ttid",
  "h5", "h5Token", "os", "clientId", "postData", "time", "requestId",
  "et", "n4h5", "sid", "chKey", "sp",
]);

function md5Hex(value) {
  return crypto.createHash("md5").update(value).digest("hex");
}

function swapSignString(value) {
  return value.slice(8, 16) + value.slice(0, 8) + value.slice(24, 32) + value.slice(16, 24);
}

function postDataMd5Hex(postData) {
  return postData ? swapSignString(md5Hex(postData)) : "";
}

function buildSignInput(params) {
  const normalized = {...params};
  if (normalized.postData) normalized.postData = postDataMd5Hex(normalized.postData);
  return Object.keys(normalized)
    .sort()
    .filter((key) => SIGN_KEYS.has(key) && normalized[key] !== undefined && normalized[key] !== null && normalized[key] !== "")
    .map((key) => `${key}=${normalized[key]}`)
    .join("||");
}

function verifyResponseSignature(response, key) {
  if (response.result === undefined || response.t === undefined || !response.sign) return false;
  const signInput = `result=${response.result}||t=${response.t}||${key.toString("utf8")}`;
  return response.sign.toLowerCase() === md5Hex(signInput).toLowerCase();
}

function decryptEt3Result(resultB64, key) {
  const encrypted = Buffer.from(resultB64, "base64");
  const nonce = encrypted.subarray(0, 12);
  const tag = encrypted.subarray(encrypted.length - 16);
  const ciphertext = encrypted.subarray(12, encrypted.length - 16);
  const decipher = crypto.createDecipheriv("aes-128-gcm", key, nonce);
  decipher.setAuthTag(tag);
  let plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  try {
    plaintext = zlib.gunzipSync(plaintext);
  } catch (_) {
    // Some responses are not gzipped.
  }
  return plaintext.toString("utf8");
}

function usage() {
  console.error(`Usage:
  tuya_mobile_crypto.js post-md5 <postData>
  tuya_mobile_crypto.js sign-input '<params-json>'
  tuya_mobile_crypto.js decrypt-response --key-hex <hex> --response '<response-json>' [--verify]`);
  process.exit(2);
}

function takeOption(args, name) {
  const index = args.indexOf(name);
  if (index === -1 || index + 1 >= args.length) usage();
  const value = args[index + 1];
  args.splice(index, 2);
  return value;
}

const args = process.argv.slice(2);
const command = args.shift();

if (command === "post-md5") {
  if (args.length !== 1) usage();
  console.log(postDataMd5Hex(args[0]));
} else if (command === "sign-input") {
  if (args.length !== 1) usage();
  console.log(buildSignInput(JSON.parse(args[0])));
} else if (command === "decrypt-response") {
  const keyHex = takeOption(args, "--key-hex");
  const responseJson = takeOption(args, "--response");
  const verify = args.includes("--verify");
  const key = Buffer.from(keyHex, "hex");
  const response = JSON.parse(responseJson);
  if (verify && !verifyResponseSignature(response, key)) {
    console.error("response signature verification failed");
    process.exit(1);
  }
  console.log(decryptEt3Result(response.result, key));
} else {
  usage();
}
