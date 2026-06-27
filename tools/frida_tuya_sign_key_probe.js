// Probe Tuya native request signing state.
//
// It verifies that doCommandNative(command=1) computes:
//   lower_hex(HMAC-SHA256(nativeSigningKey, canonicalSignInputUtf8))
//
// The native key is not printed by default. Set PRINT_NATIVE_KEY_HEX to true
// only in a private local session if the key bytes are needed for standalone
// replay tooling.

const LIB_NAME = "libthing_security.so";
const NATIVE_KEY_STRING_OFFSET = 0x3ab60;
const PRINT_NATIVE_KEY_HEX = false;
const PRINT_SIGN_INPUT = false;

function bytesToHex(bytes) {
  if (!bytes) return "";
  const out = [];
  for (let i = 0; i < bytes.length; i++) {
    let value = bytes[i];
    if (value < 0) value += 256;
    out.push(("0" + value.toString(16)).slice(-2));
  }
  return out.join("");
}

function arrayBufferToSignedBytes(buffer) {
  const view = new Uint8Array(buffer);
  const out = [];
  for (let i = 0; i < view.length; i++) {
    out.push(view[i] > 127 ? view[i] - 256 : view[i]);
  }
  return out;
}

function readLibcxxStdStringBytes(address) {
  const first = address.readU8();
  let length;
  let data;

  if ((first & 1) === 0) {
    length = first >> 1;
    data = address.add(1);
  } else {
    length = Number(address.add(8).readU64());
    data = address.add(16).readPointer();
  }

  if (length < 0 || length > 4096) {
    throw new Error("implausible std::string length: " + length);
  }

  return arrayBufferToSignedBytes(data.readByteArray(length));
}

function readNativeSigningKeyBytes() {
  const module = Process.findModuleByName(LIB_NAME);
  if (!module) throw new Error(LIB_NAME + " is not loaded");
  return readLibcxxStdStringBytes(module.base.add(NATIVE_KEY_STRING_OFFSET));
}

Java.perform(function () {
  const StringCls = Java.use("java.lang.String");
  const MessageDigest = Java.use("java.security.MessageDigest");
  const Mac = Java.use("javax.crypto.Mac");
  const SecretKeySpec = Java.use("javax.crypto.spec.SecretKeySpec");
  const ThingNetworkSecurity = Java.use("com.thingclips.sdk.network.ThingNetworkSecurity");

  function javaBytes(bytes) {
    return Java.array("byte", bytes);
  }

  function sha256Hex(bytes) {
    const md = MessageDigest.getInstance("SHA-256");
    return bytesToHex(md.digest(javaBytes(bytes)));
  }

  function hmacSha256Hex(keyBytes, messageBytes) {
    const mac = Mac.getInstance("HmacSHA256");
    mac.init(SecretKeySpec.$new(javaBytes(keyBytes), "HmacSHA256"));
    return bytesToHex(mac.doFinal(messageBytes));
  }

  function javaByteArrayToSignedBytes(value) {
    const out = [];
    for (let i = 0; i < value.length; i++) out.push(value[i]);
    return out;
  }

  function decodeUtf8(value) {
    try {
      return StringCls.$new(value, "UTF-8").toString();
    } catch (_) {
      return "<decode failed>";
    }
  }

  const doCommandNative = ThingNetworkSecurity.doCommandNative.overload(
    "android.content.Context",
    "int",
    "[B",
    "[B",
    "boolean"
  );

  doCommandNative.implementation = function (ctx, command, arg1, arg2, flag) {
    const result = doCommandNative.call(this, ctx, command, arg1, arg2, flag);

    if (command === 0 || command === 1) {
      try {
        const keyBytes = readNativeSigningKeyBytes();
        let line = "[native-sign-key] command=" + command +
          " keyLen=" + keyBytes.length +
          " keySha256=" + sha256Hex(keyBytes);
        if (PRINT_NATIVE_KEY_HEX) line += " keyHex=" + bytesToHex(keyBytes);
        console.log(line);

        if (command === 1 && arg1 !== null) {
          const computed = hmacSha256Hex(keyBytes, arg1);
          const nativeSign = result === null || result === undefined ? "" : result.toString();
          let signLine = "[native-sign-check] match=" + (computed === nativeSign) +
            " computed=" + computed +
            " native=" + nativeSign +
            " inputSha256=" + sha256Hex(javaByteArrayToSignedBytes(arg1));
          if (PRINT_SIGN_INPUT) signLine += " input=" + decodeUtf8(arg1);
          console.log(signLine);
        }
      } catch (error) {
        console.log("[native-sign-key] error=" + error);
      }
    }

    return result;
  };

  console.log("[tuya-sign-key-probe] hooks installed");
});
