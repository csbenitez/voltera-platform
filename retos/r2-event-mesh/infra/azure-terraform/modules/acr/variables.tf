variable "resource_group_name" { type = string }
variable "location" { type = string }
variable "prefix" { type = string }
variable "kubelet_object_id" {
  description = "Object ID de la identidad kubelet de AKS"
  type        = string
}
variable "tags" {
  type    = map(string)
  default = {}
}
