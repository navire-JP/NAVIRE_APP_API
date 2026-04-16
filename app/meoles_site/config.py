from pydantic_settings import BaseSettings

class MeolesSettings(BaseSettings):
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET_MEOLES: str
    BREVO_API_KEY_MEOLES: str

    # Price IDs Stripe
    PRICE_MEOLES_CUSTOM: str = "price_1TEZ9ULbFEfgkQPquMlHQqrv"
    PRICE_BAGUE_FLUID: str = "price_1TKKRWLbFEfgkQPqVnkZBBUE"
    PRICE_COLLIER_POLARIS: str = "price_1SGc0kLbFEfgkQPqZqV6sbwe"
    PRICE_COLLIER_SILENCE: str = "price_1TKKUaLbFEfgkQPqv2DUD4HS"
    PRICE_TEE_S: str = "price_1TMBmwLbFEfgkQPqNNbXpFVq"
    PRICE_TEE_M: str = "price_1TMBnxLbFEfgkQPqCVsqdmGW"
    PRICE_TEE_L: str = "price_1TMBnPLbFEfgkQPqo6qj57Qq"

    MEOLES_FRONTEND_URL: str = "https://meoles.com"
    MEOLES_API_URL: str = "https://navire-app-api.onrender.com"

    class Config:
        env_file = ".env"
        extra = "ignore"

meoles_settings = MeolesSettings()