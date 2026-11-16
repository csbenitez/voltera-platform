package main

import (
	"testing"
	"time"
)

func TestNormalizeDefaults(t *testing.T) {
	p := Params{Scenario: ScKnative}
	if err := p.normalize(); err != nil {
		t.Fatal(err)
	}
	if p.Foreign != 0.3 || p.Rate != 50 || p.N != 100 {
		t.Fatalf("defaults inesperados: %+v", p)
	}
	if err := (&Params{Scenario: "x"}).normalize(); err == nil {
		t.Fatal("escenario desconocido debe fallar")
	}
	if err := (&Params{Foreign: 2}).normalize(); err == nil {
		t.Fatal("foreign > 1 debe fallar")
	}
}

func TestRampDelayShrinks(t *testing.T) {
	p := Params{Scenario: ScRamp, Rate: 10, RateTo: 100, Duration: 10}
	_ = p.normalize()
	start, end := p.delayAfter(0, 0), p.delayAfter(0, 10*time.Second)
	if start != 100*time.Millisecond || end != 10*time.Millisecond {
		t.Fatalf("rampa: inicio=%v fin=%v", start, end)
	}
}

func TestBurstPausesAfterBurst(t *testing.T) {
	p := Params{Scenario: ScBurst, Burst: 3, Every: 2}
	_ = p.normalize()
	if p.delayAfter(0, 0) != 0 || p.delayAfter(1, 0) != 0 {
		t.Fatal("dentro de la ráfaga no hay pausa")
	}
	if p.delayAfter(2, 0) != 2*time.Second {
		t.Fatal("tras la ráfaga debe esperar every")
	}
}

func TestDone(t *testing.T) {
	p := Params{N: 5}
	if p.done(4, 0) || !p.done(5, 0) {
		t.Fatal("tope por n")
	}
	p = Params{Duration: 2}
	if p.done(100, time.Second) || !p.done(0, 2*time.Second) {
		t.Fatal("tope por duración")
	}
}
