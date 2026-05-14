package keyset

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"go_node_engine/logger"
	"os"
	"path/filepath"
	"sync"

	"github.com/tink-crypto/tink-go/v2/hybrid"
	"github.com/tink-crypto/tink-go/v2/insecurecleartextkeyset"
	"github.com/tink-crypto/tink-go/v2/keyset"
	tinktypes "github.com/tink-crypto/tink-go/v2/tink"
)

const (
	keyDir     = "/etc/oakestra/keys"
	keyFile    = "/etc/oakestra/keys/node.keyset"
	keyDirPerm = 0o700
	keyFilePerm = 0o600
)

var (
	mu              sync.Mutex
	privateHandle   *keyset.Handle
	hybridDecrypt   tinktypes.HybridDecrypt
	PublicKeysetB64 string
	KeyID           string
)

// Init loads or generates the node's HPKE X25519 keypair.
// Ensures keyDir exists with mode 0700 and the key file is mode 0600.
func Init() error {
	mu.Lock()
	defer mu.Unlock()

	if err := ensureKeyDir(); err != nil {
		return err
	}

	var err error
	if _, statErr := os.Stat(keyFile); os.IsNotExist(statErr) {
		logger.InfoLogger().Printf("Keyset: generating new HPKE keypair at %s", keyFile)
		privateHandle, err = keyset.NewHandle(hybrid.DHKEM_X25519_HKDF_SHA256_HKDF_SHA256_AES_256_GCM_Key_Template())
		if err != nil {
			return fmt.Errorf("keyset: failed to generate keypair: %w", err)
		}
		if err = savePrivateKeyset(privateHandle); err != nil {
			return fmt.Errorf("keyset: failed to save keypair: %w", err)
		}
	} else {
		logger.InfoLogger().Printf("Keyset: loading existing keypair from %s", keyFile)
		privateHandle, err = loadPrivateKeyset()
		if err != nil {
			return fmt.Errorf("keyset: failed to load keypair: %w", err)
		}
	}

	pubHandle, err := privateHandle.Public()
	if err != nil {
		return fmt.Errorf("keyset: failed to extract public keyset: %w", err)
	}

	pubBytes, err := serializePublicKeyset(pubHandle)
	if err != nil {
		return fmt.Errorf("keyset: failed to serialize public keyset: %w", err)
	}

	PublicKeysetB64 = base64.StdEncoding.EncodeToString(pubBytes)

	h := sha256.Sum256(pubBytes)
	KeyID = hex.EncodeToString(h[:8])

	hybridDecrypt, err = hybrid.NewHybridDecrypt(privateHandle)
	if err != nil {
		return fmt.Errorf("keyset: failed to create HybridDecrypt primitive: %w", err)
	}

	logger.InfoLogger().Printf("Keyset: loaded, key_id=%s", KeyID)
	return nil
}

// GetHybridDecrypt returns the cached HybridDecrypt primitive.
func GetHybridDecrypt() (tinktypes.HybridDecrypt, error) {
	mu.Lock()
	defer mu.Unlock()
	if hybridDecrypt == nil {
		return nil, fmt.Errorf("keyset not initialized — call Init() first")
	}
	return hybridDecrypt, nil
}

func ensureKeyDir() error {
	info, err := os.Stat(keyDir)
	if os.IsNotExist(err) {
		if mkErr := os.MkdirAll(keyDir, keyDirPerm); mkErr != nil {
			return fmt.Errorf("keyset: cannot create key directory %s: %w", keyDir, mkErr)
		}
		return nil
	}
	if err != nil {
		return fmt.Errorf("keyset: cannot stat key directory: %w", err)
	}
	if info.Mode().Perm() != keyDirPerm {
		logger.ErrorLogger().Printf(
			"Keyset: WARNING — key directory %s has mode %o, expected %o; tightening permissions",
			keyDir, info.Mode().Perm(), keyDirPerm,
		)
		if chErr := os.Chmod(keyDir, keyDirPerm); chErr != nil {
			return fmt.Errorf("keyset: cannot fix key directory permissions: %w", chErr)
		}
	}
	return nil
}

func savePrivateKeyset(h *keyset.Handle) error {
	f, err := os.OpenFile(filepath.Clean(keyFile), os.O_WRONLY|os.O_CREATE|os.O_TRUNC, keyFilePerm)
	if err != nil {
		return err
	}
	defer f.Close()
	return insecurecleartextkeyset.Write(h, keyset.NewBinaryWriter(f))
}

func loadPrivateKeyset() (*keyset.Handle, error) {
	f, err := os.Open(filepath.Clean(keyFile))
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return insecurecleartextkeyset.Read(keyset.NewBinaryReader(f))
}

func serializePublicKeyset(h *keyset.Handle) ([]byte, error) {
	var buf bytes.Buffer
	if err := insecurecleartextkeyset.Write(h, keyset.NewBinaryWriter(&buf)); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
