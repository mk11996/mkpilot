#pragma once

#include <QJsonObject>
#include <QNetworkReply>
#include <QString>
#include <QTimer>

#include "selfdrive/common/util.h"

namespace CommaApi {

inline QString get_base_url() {
  const std::string default_url = util::getenv("OPENPILOT_SERVER_URL", "http://192.168.5.3:18080");
  return QString::fromStdString(util::getenv("API_HOST", default_url.c_str()));
}

const QString BASE_URL = get_base_url();
QByteArray rsa_sign(const QByteArray &data);
QString create_jwt(const QJsonObject &payloads = {}, int expiry = 3600);

}  // namespace CommaApi

/**
 * Makes a request to the request endpoint.
 */

class HttpRequest : public QObject {
  Q_OBJECT

public:
  enum class Method {GET, DELETE};

  explicit HttpRequest(QObject* parent, bool create_jwt = true, int timeout = 20000);
  void sendRequest(const QString &requestURL, const Method method = Method::GET);
  bool active() const;
  bool timeout() const;

signals:
  void requestDone(const QString &response, bool success);

protected:
  QNetworkReply *reply = nullptr;

private:
  static QNetworkAccessManager *nam();
  QTimer *networkTimer = nullptr;
  bool create_jwt;

private slots:
  void requestTimeout();
  void requestFinished();
};
