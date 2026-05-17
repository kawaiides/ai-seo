resource "aws_route53_zone" "main" {
  count = var.create_dns_records ? 1 : 0
  name  = var.domain_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_route53_record" "apex" {
  count   = var.create_dns_records ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = var.domain_name
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}

resource "aws_route53_record" "www" {
  count   = var.create_dns_records ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = "www.${var.domain_name}"
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}

resource "aws_route53_record" "hooks" {
  count   = var.create_dns_records ? 1 : 0
  zone_id = aws_route53_zone.main[0].zone_id
  name    = "hooks.${var.domain_name}"
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}
