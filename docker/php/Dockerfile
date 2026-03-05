ARG PHP_VERSION=8.3
FROM php:${PHP_VERSION}-fpm-alpine

RUN apk add --no-cache \
    bash \
    curl \
    git \
    postgresql-dev \
    $PHPIZE_DEPS \
  && docker-php-ext-install pdo pdo_pgsql

WORKDIR /var/www/html
