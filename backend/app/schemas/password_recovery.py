from pydantic import BaseModel, EmailStr, Field, SecretStr, model_validator


class PasswordRecoveryCodeIssue(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=256)


class PasswordRecoveryReset(BaseModel):
    email: EmailStr
    code: SecretStr = Field(min_length=32, max_length=256)
    new_password: SecretStr = Field(min_length=8, max_length=256)
    confirm_new_password: SecretStr = Field(min_length=8, max_length=256)

    @model_validator(mode="after")
    def passwords_must_match(self):
        if (
            self.new_password.get_secret_value()
            != self.confirm_new_password.get_secret_value()
        ):
            raise ValueError("As senhas não coincidem.")
        return self
