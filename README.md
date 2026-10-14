# abdm-gateway
Flask middleware between client apps and ABDM (Ayushman Bharat Digital Mission) v3 APIs. It holds the NHA credentials, encrypts sensitive fields (RSA-OAEP) and returns one consistent JSON response format. Covers M1: ABHA creation, login/verification, profile, card and QR. Replaces the VCDoctor gateway for i-medicus.
