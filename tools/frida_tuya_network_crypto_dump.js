function safeString(value) {
  try {
    return value === null || value === undefined ? "" : value.toString();
  } catch (_) {
    return "<toString failed>";
  }
}

function bytesToHex(bytes) {
  if (!bytes) return "";
  var out = [];
  for (var i = 0; i < bytes.length; i++) {
    var value = bytes[i];
    if (value < 0) value += 256;
    out.push(("0" + value.toString(16)).slice(-2));
  }
  return out.join("");
}

function clip(value, limit) {
  value = safeString(value);
  if (value.length > limit) return value.slice(0, limit) + "...<clipped>";
  return value;
}

Java.perform(function () {
  var ThingNetworkSecurity = Java.use("com.thingclips.sdk.network.ThingNetworkSecurity");
  var ThingApiSignManager = Java.use("com.thingclips.sdk.network.ThingApiSignManager");
  var Business = Java.use("com.thingclips.smart.android.network.Business");
  var ThingApiParams = Java.use("com.thingclips.smart.android.network.ThingApiParams");

  ThingNetworkSecurity.getEncryptoKey.overload("java.lang.String", "java.lang.String").implementation = function (requestId, ecode) {
    var result = this.getEncryptoKey(requestId, ecode);
    console.log("[crypto-key] requestId=" + requestId + " ecode=" + (ecode ? "<present>" : "<null>") + " keyHex=" + bytesToHex(result));
    return result;
  };

  ThingNetworkSecurity.encryptPostData.overload("java.lang.String", "[B").implementation = function (requestId, plainBytes) {
    var plain = "";
    try {
      plain = Java.use("java.lang.String").$new(plainBytes, "UTF-8").toString();
    } catch (_) {
      plain = "<decode failed>";
    }
    var result = this.encryptPostData(requestId, plainBytes);
    console.log("[encrypt-post] requestId=" + requestId + " plain=" + clip(plain, 2000) + " cipherB64Len=" + result.length);
    return result;
  };

  ThingNetworkSecurity.doCommandNative.overload("android.content.Context", "int", "[B", "[B", "boolean").implementation = function (ctx, command, arg1, arg2, flag) {
    var arg1Text = "";
    try {
      arg1Text = Java.use("java.lang.String").$new(arg1, "UTF-8").toString();
    } catch (_) {
      arg1Text = "<decode failed>";
    }
    var result = this.doCommandNative(ctx, command, arg1, arg2, flag);
    if (command === 1) {
      console.log("[sign] input=" + clip(arg1Text, 2000) + " sign=" + safeString(result));
    } else {
      console.log("[native-command] command=" + command + " input=" + clip(arg1Text, 500) + " result=" + clip(result, 500));
    }
    return result;
  };

  ThingApiSignManager.generateSignatureSdk.implementation = function (map) {
    var result = this.generateSignatureSdk(map);
    console.log("[generate-signature-sdk] result=" + result + " map=" + clip(map, 2500));
    return result;
  };

  ThingApiParams.getRequestBody.implementation = function () {
    var api = safeString(this.getApiName());
    var version = safeString(this.getApiVersion());
    var result = this.getRequestBody();
    console.log("[request-body] api=" + api + " v=" + version + " requestId=" + safeString(this.getRequestId()) + " body=" + clip(result, 3000));
    return result;
  };

  Business.decryptResponse.overload("com.thingclips.smart.android.network.ThingApiParams", "java.lang.String", "java.util.List").implementation = function (apiParams, raw, headers) {
    var api = safeString(apiParams.getApiName());
    var version = safeString(apiParams.getApiVersion());
    var requestId = safeString(apiParams.getRequestId());
    var result = this.decryptResponse(apiParams, raw, headers);
    console.log("[decrypt-response] api=" + api + " v=" + version + " requestId=" + requestId + " plaintext=" + clip(result, 8000));
    return result;
  };

  console.log("[tuya-network-crypto-dump] hooks installed");
});
