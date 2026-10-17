package domain

import "testing"

func TestHomeRegion(t *testing.T) {
	cases := map[string]string{
		"AMI-onprem-0001": RegionOnprem,
		"AMI-cloud-0007":  RegionCloud,
		"AMI-mars-0001":   "",
		"medidor":         "",
	}
	for in, want := range cases {
		if got := HomeRegion(in); got != want {
			t.Errorf("HomeRegion(%q)=%q, want %q", in, got, want)
		}
	}
	if OtherRegion(RegionCloud) != RegionOnprem || OtherRegion(RegionOnprem) != RegionCloud {
		t.Error("OtherRegion")
	}
}
