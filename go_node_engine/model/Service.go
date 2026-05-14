package model

import "encoding/json"

// SealedCredential is an HPKE-sealed credential delivered alongside a deploy payload.
// The ciphertext is sealed to this worker's public key and bound to the deployment context
// via HPKE AAD so it cannot be replayed across jobs or workers.
type SealedCredential struct {
	UseAs          string `json:"use_as"`
	Type           string `json:"type"`
	CredentialID   string `json:"credential_id"`
	InstanceNumber int    `json:"instance_number"`
	ciphertextB64  string // never serialised — redacted in MarshalJSON
	KeyID          string `json:"key_id"`
	UnixTS         int64  `json:"unix_ts"`
}

// UnmarshalJSON reads the ciphertext from the wire format without ever logging it.
func (s *SealedCredential) UnmarshalJSON(data []byte) error {
	var raw struct {
		UseAs          string `json:"use_as"`
		Type           string `json:"type"`
		CredentialID   string `json:"credential_id"`
		InstanceNumber int    `json:"instance_number"`
		CiphertextB64  string `json:"ciphertext_b64"`
		KeyID          string `json:"key_id"`
		UnixTS         int64  `json:"unix_ts"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	s.UseAs = raw.UseAs
	s.Type = raw.Type
	s.CredentialID = raw.CredentialID
	s.InstanceNumber = raw.InstanceNumber
	s.ciphertextB64 = raw.CiphertextB64
	s.KeyID = raw.KeyID
	s.UnixTS = raw.UnixTS
	return nil
}

// MarshalJSON never emits the ciphertext — only metadata fields.
func (s SealedCredential) MarshalJSON() ([]byte, error) {
	return json.Marshal(struct {
		UseAs          string `json:"use_as"`
		Type           string `json:"type"`
		CredentialID   string `json:"credential_id"`
		InstanceNumber int    `json:"instance_number"`
		KeyID          string `json:"key_id"`
		UnixTS         int64  `json:"unix_ts"`
	}{
		UseAs:          s.UseAs,
		Type:           s.Type,
		CredentialID:   s.CredentialID,
		InstanceNumber: s.InstanceNumber,
		KeyID:          s.KeyID,
		UnixTS:         s.UnixTS,
	})
}

// CiphertextB64 returns the raw ciphertext for the credentials package only.
func (s *SealedCredential) CiphertextB64() string {
	return s.ciphertextB64
}

// VolumeRequest describes a CSI volume that must be mounted for this service.
type VolumeRequest struct {
	// VolumeID is a unique user-defined identifier for the volume claim
	VolumeID string `json:"volume_id"`
	// CSIDriver is the CSI driver name (must match an available CSIDriverType on the node)
	CSIDriver string `json:"csi_driver"`
	// MountPath is the absolute path inside the container where the volume will be bind-mounted
	MountPath string `json:"mount_path"`
	// Config holds driver-specific parameters that are forwarded verbatim to the CSI plugin
	Config map[string]string `json:"config"`
}

// Service is the struct that describes the service
type Service struct {
	JobID           string          `json:"_id"`
	Sname           string          `json:"job_name"`
	Instance        int             `json:"instance_number"`
	Image           string          `json:"image"`
	Commands        []string        `json:"cmd"`
	Env             []string        `json:"environment"`
	Ports           string          `json:"port"`
	Status          string          `json:"status"`
	Runtime         string          `json:"virtualization"`
	Platform        string          `json:"platform"`
	StatusDetail    string          `json:"status_detail"`
	Vtpus           int             `json:"vtpus"`
	Vgpus           int             `json:"vgpus"`
	Vcpus           int             `json:"vcpus"`
	Memory          int             `json:"memory"`
	UnikernelImages []string        `json:"vm_images"`
	Architectures   []string        `json:"arch"`
	Volumes         []VolumeRequest `json:"volumes"`
	Storage         int             `json:"storage"`
	Pid         int
	OneShot     bool               `json:"one_shot"`
	Privileged  bool               `json:"privileged"`
	Credentials []SealedCredential `json:"credentials_sealed,omitempty"`
}

// Redacted returns a copy of the service with credentials stripped for safe logging.
func (s Service) Redacted() Service {
	s.Credentials = nil
	return s
}

// Resources is the struct that describes the resources
type Resources struct {
	Cpu      string `json:"cpu_percent"`
	Memory   string `json:"memory_percent"`
	Disk     string `json:"disk"`
	Logs     string `json:"logs"`
	Sname    string `json:"job_name"`
	Runtime  string `json:"virtualization"`
	Instance int    `json:"instance"`
}

// ServiceStatus is the struct that describes the service status
const (
	SERVICE_CREATING = "CREATING"
	// SERVICE_CREATED means the service was started and is currently running.
	// This status is managed from outside the runtimes.
	SERVICE_CREATED = "CREATED"
	// SERVICE_FAILED means starting the service failed.
	// This status is managed from outside the runtimes.
	SERVICE_FAILED = "FAILED"
	// SERVICE_DEAD means the service exited without being undeployed/stopped
	// and is not a one-shot service or exited with an error.
	// This status managed by the individual runtimes.
	SERVICE_DEAD = "DEAD"
	// SERVICE_COMPLETED means the service exited without being undeployed/stopped
	// and is a one-shot service and exited successfully.
	// This status managed by the individual runtimes.
	SERVICE_COMPLETED = "COMPLETED"
	// SERVICE_UNDEPLOYED means the service was undeployed successfully and is not running anymore.
	// This status is managed from outside the runtimes.
	SERVICE_UNDEPLOYED = "UNDEPLOYED"
)
