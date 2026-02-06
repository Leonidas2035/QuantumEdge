import os
import pytest
from cryptography.fernet import Fernet
from tools.secure_env import encrypt_env, decrypt_env

@pytest.fixture
def temp_env(tmp_path):
    env_file = tmp_path / ".env"
    enc_file = tmp_path / ".env.enc"
    
    env_file.write_text("TEST_SECRET=super_secret_value\nANOTHER_VAR=123", encoding="utf-8")
    
    key = Fernet.generate_key().decode()
    
    return env_file, enc_file, key

def test_encryption_flow(temp_env):
    env_file, enc_file, key = temp_env
    
    # Encrypt
    encrypt_env(env_file, enc_file, key)
    assert enc_file.exists()
    assert enc_file.read_bytes() != env_file.read_bytes()
    
    # Decrypt logic verification (decrypt_env modifies os.environ, so we need to mock or inspect)
    # We'll just run it and check os.environ
    
    # Clear env var if exists
    if "TEST_SECRET" in os.environ:
        del os.environ["TEST_SECRET"]
        
    decrypt_env(enc_file, key)
    
    assert os.environ.get("TEST_SECRET") == "super_secret_value"
    assert os.environ.get("ANOTHER_VAR") == "123"

def test_missing_key_behavior(temp_env):
    env_file, enc_file, key = temp_env
    encrypt_env(env_file, enc_file, key)
    
    # Wrong key
    wrong_key = Fernet.generate_key().decode()
    
    # Should not crash but print error (or catch exception depending on implementation)
    # The current implementation catches generic Exception and prints it
    
    if "TEST_SECRET" in os.environ:
        del os.environ["TEST_SECRET"]

    decrypt_env(enc_file, wrong_key)
    
    assert "TEST_SECRET" not in os.environ
