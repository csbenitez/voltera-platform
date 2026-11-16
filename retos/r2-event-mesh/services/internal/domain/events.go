// Package domain define topics, tipos CloudEvent y payloads del lab EDA.
package domain

import (
	"errors"
	"math"
	"strings"
	"time"
)

// Topics de Redpanda (mismos nombres en ambas regiones).
const (
	TopicIngress   = "voltera.ingress.declarations"       // Cola de Declaración de Posición (AMI)
	TopicPositions = "voltera.position.declared.v1"       // PositionDeclared
	TopicPrices    = "voltera.price.kwh.v1"               // Precio kWh
	TopicMatched   = "voltera.energy.matched.v1"          // EnergyMatched
	TopicHeartbeat = "voltera.mesh.heartbeat.v1"          // latido del matching-engine
	TopicFailover  = "voltera.mesh.failover.positions.v1" // posiciones/precios reenviados por el mesh (solo nube)
	TopicBilling   = "voltera.mesh.billing.v1"            // salida consolidada del mesh hacia Facturación
	TopicForward   = "voltera.declaration.forward.v1"     // declaraciones de medidores de otra región (Knative KafkaSource)
	TopicDLQ       = "voltera.declaration.dlq.v1"         // dead-letter del Trigger de Knative
)

// Tipos CloudEvent.
const (
	TypeDeclaration = "com.voltera.ami.declaration.v1"
	TypePosition    = "com.voltera.position.declared.v1"
	TypePrice       = "com.voltera.price.kwh.v1"
	TypeMatched     = "com.voltera.energy.matched.v1"
	TypeHeartbeat   = "com.voltera.mesh.heartbeat.v1"
	TypeForwarded   = "com.voltera.ami.declaration.forwarded.v1"
)

// Regiones del lab.
const (
	RegionOnprem = "onprem"
	RegionCloud  = "cloud"
)

// HomeRegion devuelve la región dueña de un medidor según su ID "AMI-<region>-NNNN".
// Vacío si el ID no sigue el formato (se trata como propio).
func HomeRegion(meterID string) string {
	parts := strings.Split(meterID, "-")
	if len(parts) < 3 || parts[0] != "AMI" {
		return ""
	}
	if parts[1] == RegionOnprem || parts[1] == RegionCloud {
		return parts[1]
	}
	return ""
}

// OtherRegion devuelve la región contraria a la dada.
func OtherRegion(region string) string {
	if region == RegionCloud {
		return RegionOnprem
	}
	return RegionCloud
}

const (
	Surplus = "SURPLUS"
	Deficit = "DEFICIT"
)

// Declaration es la lectura sintética que inyecta el script (medidor AMI).
type Declaration struct {
	MeterID    string    `json:"meter_id"`
	Kind       string    `json:"kind"` // SURPLUS | DEFICIT
	KWh        float64   `json:"kwh"`
	MeasuredAt time.Time `json:"measured_at"`
}

func (d Declaration) Validate() error {
	if d.MeterID == "" {
		return errors.New("meter_id requerido")
	}
	if d.Kind != Surplus && d.Kind != Deficit {
		return errors.New("kind debe ser SURPLUS o DEFICIT")
	}
	if d.KWh <= 0 || d.KWh > 1000 {
		return errors.New("kwh fuera de rango")
	}
	return nil
}

type PositionDeclared struct {
	PositionID string    `json:"position_id"`
	MeterID    string    `json:"meter_id"`
	Kind       string    `json:"kind"`
	KWh        float64   `json:"kwh"`
	DeclaredAt time.Time `json:"declared_at"`
	Region     string    `json:"region"`
}

type PriceKWh struct {
	PricePerKWh float64   `json:"price_per_kwh"`
	Currency    string    `json:"currency"`
	ValidFrom   time.Time `json:"valid_from"`
	Region      string    `json:"region"`
}

type EnergyMatched struct {
	MatchID        string    `json:"match_id"`
	SellerPosition string    `json:"seller_position"`
	BuyerPosition  string    `json:"buyer_position"`
	SellerMeter    string    `json:"seller_meter"`
	BuyerMeter     string    `json:"buyer_meter"`
	KWh            float64   `json:"kwh"`
	PricePerKWh    float64   `json:"price_per_kwh"`
	Total          float64   `json:"total"`
	Currency       string    `json:"currency"`
	OriginRegion   string    `json:"origin_region"` // región donde se declararon las posiciones
	ProcessedBy    string    `json:"processed_by"`  // región cuyo matching-engine hizo el calce
	MatchedAt      time.Time `json:"matched_at"`
}

func (m EnergyMatched) Validate() error {
	switch {
	case m.MatchID == "" || m.SellerPosition == "" || m.BuyerPosition == "":
		return errors.New("ids incompletos")
	case m.KWh <= 0 || m.PricePerKWh <= 0:
		return errors.New("kwh/precio deben ser > 0")
	case math.Abs(m.Total-m.KWh*m.PricePerKWh) > 0.01:
		return errors.New("total != kwh * precio")
	}
	return nil
}

type Heartbeat struct {
	Service        string    `json:"service"`
	Region         string    `json:"region"`
	At             time.Time `json:"at"`
	LastPositionAt time.Time `json:"last_position_at"` // ce-time de la última posición procesada
}

func Round(v float64, decimals int) float64 {
	p := math.Pow(10, float64(decimals))
	return math.Round(v*p) / p
}
