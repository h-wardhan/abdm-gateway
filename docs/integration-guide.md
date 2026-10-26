# Integration guide

This guide shows how a client application integrates with abdm-gateway for ABDM Milestone 1:
ABHA creation, ABHA login, and fetching the profile, card and QR. The API is plain HTTPS + JSON,
so any stack works. Examples are given in curl, PHP (Laravel/Guzzle), Node.js, Java and C#.

- Base URL (`{baseUrl}`): `https://abdm-gateway.example.com`. For local development, use `http://localhost:5000`.
- Swagger UI: https://abdm-gateway.example.com/docs. OpenAPI spec: https://abdm-gateway.example.com/swagger.json.
- Postman: import [postman_collection.json](postman_collection.json) and set `baseUrl`, `clientId` and `clientSecret`.

## 1. Get credentials
The gateway operator runs:
```
flask clients create --name "<your app>"
```
and sends you the **Client-Id** and **Client-Secret**. The secret is shown only once, so store it
the way you store other secrets (environment variable, vault).

## 2. Authenticate
```
POST /api/v1/auth/session
{"clientId": "...", "clientSecret": "..."}
→ {"success": true, "data": {"accessToken": "...", "expiresIn": 1800, "refreshToken": "...", "refreshExpiresIn": 86400, "tokenType": "Bearer"}}
```
Send `Authorization: <accessToken>` on every other call. The `Bearer ` prefix is optional.
Cache the token until it expires, and when it does, either call `/api/v1/auth/refresh` with
`{"refreshToken": "..."}` or create a new session.

Optional headers:
- `Client-Id`: if you send it, it must match the token.
- `REQUEST-ID`: your own correlation id. It is echoed back as `requestId` and in the `X-Request-ID` header.

## 3. Response envelope
```json
{ "success": true,  "data": { ... }, "message": "ABDM message, if any", "requestId": "..." }
{ "success": false, "code": "error", "message": { "error": { "code": "...", "message": "..." } }, "requestId": "..." }
```
- Errors use HTTP 4xx/5xx. The full list is in [error-codes.md](error-codes.md).
- **A wrong OTP on a verify call is the exception:** it returns HTTP 200 with `success: false` and
  `data.authResult: "failed"`. Always check `success`.

## 4. Two kinds of tokens
| Token | Where from | Used for |
|---|---|---|
| access token | `/api/v1/auth/session` | `Authorization` header on every call (identifies your app) |
| ABHA user token (`xToken`) | any **verify** response: `data.token`, or `data.tokens.token` for ABHA address login, or `data.tokens.token` after enrolment | `?xToken=` query param or `X-Token` header on profile/card/QR/email calls (identifies the patient) |

The ABHA user token expires after about 30 minutes. When it does, ABDM returns 401 and you get `ABHA_TOKEN_INVALID` or ABDM's code.

## 5. Flows

### 5.1 Create ABHA (Aadhaar OTP)
1. `POST /api/v1/abha/enrol/aadhaar/otp` with `{aadhaarNumber}`. Returns `data.txnId`.
2. `POST /api/v1/abha/enrol/aadhaar/verify` with `{txnId, otpValue, mobilenumber}`. Returns
   `data.abhaProfile` (`abhaNumber`, names, `dob`, `gender`, `mobile`, `phrAddress[]`, …), `data.tokens`
   and `data.isNew`.
3. If `data.abhaProfile.mobile` is empty or different from the mobile you sent:
   - `POST /api/v1/abha/enrol/mobile/otp` with `{txnId, mobilenumber}`
   - `POST /api/v1/abha/enrol/mobile/verify` with `{txnId, otp}`
4. `GET /api/v1/abha/enrol/address-suggestions?txnId=...` returns `data.abhaAddressList[]`.
5. `POST /api/v1/abha/enrol/address` with `{txnId, abhaAddress, preferred: 1}`. Send the address without the `@sbx` / `@abdm` suffix.
6. Optional: `POST /api/v1/abha/profile/email/verify-link` with `{email}` and `X-Token: <data.tokens.token>`.

### 5.2 Login by mobile
1. `POST /api/v1/abha/search/mobile` with `{mobileNumber}`. Returns `data: [{txnId, ABHA:[{index, ABHANumber, name, gender}]}]`.
2. `POST /api/v1/abha/login/mobile/otp` with `{txnId, index}`, using the `index` of the ABHA the user picked.
3. `POST /api/v1/abha/login/mobile/verify` with `{txnId, otp}`. Returns `data.token` (the ABHA user token).

### 5.3 Login by ABHA number
1. Optional: `POST /api/v1/abha/search/abha-number` with `{abhaNumber}`. Returns `authMethods`.
2. `POST /api/v1/abha/login/abha-number/{mobile|aadhaar}/otp` with `{abhaNumber}`.
3. `POST /api/v1/abha/login/abha-number/{mobile|aadhaar}/verify` with `{txnId, otp}`. Use the same method as step 2.

### 5.4 Login by Aadhaar
1. `POST /api/v1/abha/login/aadhaar/otp` with `{aadhaarNumber}`.
2. `POST /api/v1/abha/login/aadhaar/verify` with `{txnId, otp}`.

### 5.5 Login by ABHA address
1. `POST /api/v1/abha/search/abha-address` with `{abhaAddress}`. Returns `data.authMethods` (`MOBILE_OTP`, `AADHAAR_OTP`).
2. `POST /api/v1/abha/login/abha-address/otp` with `{abhaAddress, scope: "mobile-verify" | "aadhaar-verify"}`.
3. `POST /api/v1/abha/login/abha-address/verify` with `{txnId, otp, scope}`. Returns `data.tokens.token` and `data.users[]`.
4. Use `/api/v1/abha/phr/profile` and `/api/v1/abha/phr/card` for this login type.

### 5.6 Profile, card, QR
| Call | Returns |
|---|---|
| `GET /api/v1/abha/profile?xToken=...` | JSON profile, with ABDM's keys unchanged |
| `GET /api/v1/abha/profile/card?xToken=...` | ABHA card bytes (`image/png` or `application/pdf`) |
| `GET /api/v1/abha/profile/qr?xToken=...` | QR PNG bytes |

If any of these fails, the response is JSON in the error envelope.

## 6. Examples

### curl
```bash
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/session -H 'Content-Type: application/json' \
  -d '{"clientId":"'$CID'","clientSecret":"'$CSECRET'"}' | jq -r .data.accessToken)

curl -s -X POST $BASE/api/v1/abha/enrol/aadhaar/otp -H "Authorization: $TOKEN" \
  -H 'Content-Type: application/json' -d '{"aadhaarNumber":"999941057058"}'

curl -s "$BASE/api/v1/abha/profile/card?xToken=$XTOKEN" -H "Authorization: $TOKEN" -o abha-card.png
```

### PHP (Laravel / Guzzle)
```php
$client = new \GuzzleHttp\Client(['base_uri' => config('abdm.base_url')]);
$session = json_decode($client->post('api/v1/auth/session', ['json' => [
    'clientId' => config('abdm.client_id'), 'clientSecret' => config('abdm.client_secret'),
]])->getBody(), true);
$token = $session['data']['accessToken'];

try {
    $res = $client->post('api/v1/abha/enrol/aadhaar/otp', [
        'headers' => ['Authorization' => $token, 'REQUEST-ID' => (string) \Str::uuid()],
        'json' => ['aadhaarNumber' => $aadhaar],
    ]);
    $txnId = json_decode($res->getBody(), true)['data']['txnId'];
} catch (\GuzzleHttp\Exception\RequestException $e) {
    $err = json_decode((string) $e->getResponse()->getBody(), true);
    $message = $err['message']['error']['message'] ?? 'ABDM error';
}
```

### Node.js (fetch)
```js
const base = process.env.ABDM_GATEWAY_URL;
const { data: { accessToken } } = await (await fetch(`${base}/api/v1/auth/session`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ clientId: process.env.CLIENT_ID, clientSecret: process.env.CLIENT_SECRET }),
})).json();

const res = await fetch(`${base}/api/v1/abha/login/mobile/verify`, {
  method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: accessToken },
  body: JSON.stringify({ txnId, otp }),
});
const body = await res.json();
if (!body.success) throw new Error(body.data?.message || body.message?.error?.message);
const xToken = body.data.token;
```

### Java (java.net.http)
```java
HttpClient http = HttpClient.newHttpClient();
HttpRequest req = HttpRequest.newBuilder(URI.create(base + "/api/v1/abha/profile/card?xToken=" + xToken))
    .header("Authorization", accessToken).GET().build();
HttpResponse<byte[]> res = http.send(req, HttpResponse.BodyHandlers.ofByteArray());
if (res.statusCode() == 200) Files.write(Path.of("abha-card.png"), res.body());
```

### C# (.NET HttpClient)
```csharp
var http = new HttpClient { BaseAddress = new Uri(baseUrl) };
http.DefaultRequestHeaders.TryAddWithoutValidation("Authorization", accessToken);
var res = await http.PostAsJsonAsync("api/v1/abha/search/abha-address", new { abhaAddress = "ramesh.kumar@sbx" });
var json = await res.Content.ReadFromJsonAsync<JsonElement>();
var methods = json.GetProperty("data").GetProperty("authMethods");
```

## 7. Good practices
- Never log Aadhaar numbers, OTPs or tokens on your side either.
- Don't retry OTP requests automatically. ABDM throttles them, and you get a 429 with
  "You can request for new OTP after N seconds".
- Keep the `txnId` only for the duration of a flow. Each client can only continue its own transactions.
- Use the `requestId` from responses when you report an issue.
